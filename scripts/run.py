#!/usr/bin/env python3
"""唯一入口：读 input/ 的讨论点 JSON，调模型生成对话，解析、质检、落盘。

    python scripts/run.py            打印预估，等确认后跑
    python scripts/run.py --yes      不问直接跑
    python scripts/run.py --plan     只打印预估，不跑

API key 从环境变量 DEEPSEEK_API_KEY 读，不写进 config。
"""

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check as checker  # noqa: E402
import parse as parser_  # noqa: E402

CHARS_PER_TOKEN = 1.5  # 中文粗估


def load_config():
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def build_system(cfg):
    """prompts/system.md 填入人设和三份 references。

    用 replace 不用 format，因为 references 里有 JSON 示例带大括号。
    """
    tpl = (ROOT / "prompts" / "system.md").read_text(encoding="utf-8")
    slots = {
        "{persona}": ROOT / cfg["persona"],
        "{words}": ROOT / "references" / "words.md",
        "{alive_dialogue}": ROOT / "references" / "alive-dialogue.md",
        "{knowledge_honesty}": ROOT / "references" / "knowledge-honesty.md",
    }
    for k, path in slots.items():
        tpl = tpl.replace(k, path.read_text(encoding="utf-8").strip())
    return tpl


def build_user(cfg, domain, opinion, keyword, chunk):
    tpl = (ROOT / "prompts" / "user.md").read_text(encoding="utf-8")
    points = "\n".join(f"{d['id']}  {d['point']}" for d in chunk)
    for k, v in {
        "{domain}": domain,
        "{keyword}": keyword,
        "{opinion}": opinion,
        "{n}": str(len(chunk)),
        "{points}": points,
        "{turns}": str(cfg["turns"]),
        "{answer_length}": cfg["profiles"][cfg["profile"]],
    }.items():
        tpl = tpl.replace(k, v)
    return tpl


def build_tasks(cfg):
    """展开成 [(domain, keyword, chunk_idx, chunk, opinion), ...]"""
    tasks, skipped = [], []
    size = cfg["dialogue_gen_per_call"]
    for path in sorted((ROOT / cfg["input_dir"]).glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        domain, opinion = data["domain"], data.get("opinion", "").strip()
        if not opinion:
            skipped.append(f"{path.name}：opinion 空着")
            continue
        for topic in data["topics"]:
            kw, ds = topic["keyword"], topic["discussions"]
            for i in range(0, len(ds), size):
                tasks.append((domain, kw, i // size + 1, ds[i : i + size], opinion))
    return tasks, skipped


def print_plan(cfg, tasks, skipped, system):
    for s in skipped:
        print(f"跳过  {s}", file=sys.stderr)

    by_domain = {}
    for domain, kw, _, chunk, _ in tasks:
        d = by_domain.setdefault(domain, {})
        d[kw] = d.get(kw, [0, 0])
        d[kw][0] += len(chunk)
        d[kw][1] += 1

    total_dialogues = sum(len(t[3]) for t in tasks)
    for domain, kws in by_domain.items():
        print(f"\n{domain}  {len(kws)} 个关键词，{sum(v[0] for v in kws.values())} 条讨论点")
        for kw, (pts, calls) in kws.items():
            print(f"  {kw:<10} {pts:>3} 条讨论点  →  {calls} 次调用")

    sys_tok = len(system) / CHARS_PER_TOKEN
    in_tok = sys_tok * len(tasks) + 300 * len(tasks)
    out_tok = total_dialogues * cfg["turns"] * 2 * 25 / CHARS_PER_TOKEN

    print(f"\n{len(tasks)} 次调用  →  {total_dialogues} 段对话")
    print(f"参数 {cfg['profile']} / {cfg['turns']} 轮")
    print(f"预估 输入 {in_tok/10000:.1f} 万 token，输出 {out_tok/10000:.1f} 万 token", end="")
    pi, po = cfg.get("price_per_1m_input"), cfg.get("price_per_1m_output")
    if pi and po:
        print(f"，约 ¥{in_tok/1e6*pi + out_tok/1e6*po:.2f}")
    else:
        print()
    return total_dialogues


def call_once(client, cfg, system, task):
    domain, kw, idx, chunk, opinion = task
    user = build_user(cfg, domain, opinion, kw, chunk)
    want = {d["id"] for d in chunk}

    got = set()
    for attempt in range(cfg["max_retry"] + 1):
        rsp = client.chat.completions.create(
            model=cfg["model"],
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        last = rsp.choices[0].message.content
        got = set(re.findall(r"^===\s*(\S+)", last, re.M))
        if got == want:
            out = ROOT / cfg["output_dir"] / "raw" / f"{domain}_{kw}_{idx}.txt"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(last, encoding="utf-8")
            return task, None
        if attempt < cfg["max_retry"]:
            miss = "、".join(sorted(want - got))
            user += f"\n\n上一次输出缺了这几条：{miss}。全部重写。"
    return task, "缺 " + "、".join(sorted(want - got))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="不问直接跑")
    ap.add_argument("--plan", action="store_true", help="只打印预估")
    args = ap.parse_args()

    cfg = load_config()
    if cfg["profile"] not in cfg["profiles"]:
        sys.exit(f"config.yaml 的 profile 是 {cfg['profile']}，profiles 里没有")

    system = build_system(cfg)
    tasks, skipped = build_tasks(cfg)
    if not tasks:
        sys.exit(f"{cfg['input_dir']} 里没有可用的讨论点 JSON")

    print_plan(cfg, tasks, skipped, system)
    if args.plan:
        return
    if not args.yes and input("\n继续？[y/N] ").strip().lower() != "y":
        return

    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        sys.exit("环境变量 DEEPSEEK_API_KEY 没设")
    from openai import OpenAI

    client = OpenAI(api_key=key, base_url="https://api.deepseek.com")

    print()
    fails = []
    with ThreadPoolExecutor(max_workers=cfg["concurrency"]) as pool:
        futs = {pool.submit(call_once, client, cfg, system, t): t for t in tasks}
        for i, fut in enumerate(as_completed(futs), 1):
            task, err = fut.result()
            domain, kw, idx, chunk, _ = task
            tag = f"{domain}/{kw} 第{idx}批"
            print(f"  [{i}/{len(tasks)}] {tag:<28} {'ok' if not err else err}")
            if err:
                fails.append(f"{tag}: {err}")

    if fails:
        print(f"\n{len(fails)} 批没跑成，raw 不完整：", file=sys.stderr)
        for f in fails:
            print(f"  {f}", file=sys.stderr)

    print()
    result = parser_.parse_all(cfg)
    print()
    checker.check_all(cfg)
    for path in result:
        print(f"\n✓ {path}")


if __name__ == "__main__":
    main()
