#!/usr/bin/env python3
"""唯一入口：读 input/ 的讨论点，调模型生成对话，解析、质检、落盘。

    python scripts/run.py            打印预估，等确认后跑
    python scripts/run.py --yes      不问直接跑
    python scripts/run.py --plan     只打印预估，不发请求
    python scripts/run.py --preview  打印第一批拼出来的 system 和 user，不发请求

这个文件不碰提示词也不碰 HTTP，全在 scripts/llm.py 里。
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check as checker  # noqa: E402
import llm  # noqa: E402
import parse as parser_  # noqa: E402


def build_tasks(cfg):
    """扫 input/ 下所有 JSON，按 keyword 切成一次调用一批。

    一次调用只带一个 keyword 的 dialogue_gen_per_call 条讨论点，上下文干净。
    opinion 空着的文件整个跳过。

    Args:
        cfg (dict): llm.load_config() 的返回值。

    Returns:
        tuple[list[dict], list[str]]: (任务列表, 跳过原因)。
            每个任务形如 {"domain", "keyword", "batch", "points", "opinion"}。
    """
    tasks, skipped = [], []
    size = cfg["dialogue_gen_per_call"]
    for path in sorted((ROOT / cfg["input_dir"]).glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        opinion = data.get("opinion", "").strip()
        if not opinion:
            skipped.append(f"{path.name}：opinion 空着")
            continue
        for topic in data["topics"]:
            points = topic["discussions"]
            for i in range(0, len(points), size):
                tasks.append({
                    "domain": data["domain"],
                    "keyword": topic["keyword"],
                    "batch": i // size + 1,
                    "points": points[i:i + size],
                    "opinion": opinion,
                })
    return tasks, skipped


def tag(task):
    """任务的一行标识，打印用。

    Args:
        task (dict): build_tasks() 里的一项。

    Returns:
        str: 形如「两性关系/彩礼 第1批」。
    """
    return f"{task['domain']}/{task['keyword']} 第{task['batch']}批"


def print_plan(cfg, tasks, skipped, system):
    """跑之前把要花的钱和要产的量打出来。

    Args:
        cfg (dict): llm.load_config() 的返回值。
        tasks (list[dict]): build_tasks() 的任务列表。
        skipped (list[str]): build_tasks() 的跳过原因。
        system (str): llm.build_system() 的结果。
    """
    for s in skipped:
        print(f"跳过  {s}", file=sys.stderr)

    by_domain = {}
    for t in tasks:
        kw = by_domain.setdefault(t["domain"], {}).setdefault(t["keyword"], [0, 0])
        kw[0] += len(t["points"])
        kw[1] += 1

    for domain, keywords in by_domain.items():
        total = sum(v[0] for v in keywords.values())
        print(f"\n{domain}  {len(keywords)} 个关键词，{total} 条讨论点")
        for kw, (points, calls) in keywords.items():
            print(f"  {kw:<10} {points:>3} 条讨论点  →  {calls} 次调用")

    n_dialogues = sum(len(t["points"]) for t in tasks)
    users = [llm.build_user(cfg, t["domain"], t["keyword"], t["opinion"], t["points"])
             for t in tasks]
    usage = llm.estimate_usage(cfg, system, users, n_dialogues)

    print(f"\n{len(tasks)} 次调用  →  {n_dialogues} 段对话")
    print(f"参数 {cfg['model']}  {cfg['profile']}  {cfg['turns']} 轮  "
          f"思考{'开' if cfg['thinking'] else '关'}")
    print(f"预估 {llm.format_usage(cfg, usage)}")


def print_preview(cfg, tasks, system):
    """把第一批实际会发出去的 system 和 user 原样打出来。

    Args:
        cfg (dict): llm.load_config() 的返回值。
        tasks (list[dict]): build_tasks() 的任务列表。
        system (str): llm.build_system() 的结果。
    """
    t = tasks[0]
    user = llm.build_user(cfg, t["domain"], t["keyword"], t["opinion"], t["points"])
    for name, text in (("system", system), ("user", user)):
        print(f"\n{'=' * 30} {name}  {len(text)} 字 {'=' * 30}\n")
        print(text)
    print(f"\n{'=' * 70}")
    print(f"以上是「{tag(t)}」的两条消息，共 {len(tasks)} 批，system 每批相同。")


def run_one(cfg, system, task):
    """跑一批：调模型，成功就把原文写进 output/raw/。

    Args:
        cfg (dict): llm.load_config() 的返回值。
        system (str): llm.build_system() 的结果。
        task (dict): build_tasks() 里的一项。

    Returns:
        tuple[dict, dict, str | None]: (任务, 用量, 错在哪)。成功时第三项是 None。
    """
    text, usage, error = llm.generate(
        cfg, system, task["domain"], task["keyword"], task["opinion"], task["points"])
    if error:
        return task, usage, error

    out = ROOT / cfg["output_dir"] / "raw" / f"{task['domain']}_{task['keyword']}_{task['batch']}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return task, usage, None


def main():
    """入口：预估 → 确认 → 并发生成 → 解析 → 质检。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="不问直接跑")
    ap.add_argument("--plan", action="store_true", help="只打印预估")
    ap.add_argument("--preview", action="store_true", help="只打印拼好的 system 和 user")
    args = ap.parse_args()

    cfg = llm.load_config()
    if cfg["profile"] not in cfg["profiles"]:
        sys.exit(f"config.yaml 的 profile 是 {cfg['profile']}，profiles 里没有这一档")
    if not cfg.get("api_key"):
        sys.exit("config.yaml 里没写 api_key")

    system = llm.build_system(cfg)
    tasks, skipped = build_tasks(cfg)
    if not tasks:
        sys.exit(f"{cfg['input_dir']} 里没有可用的讨论点 JSON")

    if args.preview:
        print_preview(cfg, tasks, system)
        return

    print_plan(cfg, tasks, skipped, system)
    if args.plan:
        return
    if not args.yes and input("\n继续？[y/N] ").strip().lower() != "y":
        return

    print()
    total = llm.blank_usage()
    fails = []
    with ThreadPoolExecutor(max_workers=cfg["concurrency"]) as pool:
        futures = [pool.submit(run_one, cfg, system, t) for t in tasks]
        for i, future in enumerate(as_completed(futures), 1):
            task, usage, error = future.result()
            for k in total:
                total[k] += usage[k]
            print(f"  [{i}/{len(tasks)}] {tag(task):<28} {error or 'ok'}")
            if error:
                fails.append(f"{tag(task)}: {error}")

    print(f"\n实际 {llm.format_usage(cfg, total)}")
    if fails:
        print(f"\n{len(fails)} 批没跑成，raw 不完整：", file=sys.stderr)
        for f in fails:
            print(f"  {f}", file=sys.stderr)

    print()
    written = parser_.parse_all(cfg)
    print()
    checker.check_all(cfg)
    for path in written:
        print(f"\n✓ {path}")


if __name__ == "__main__":
    main()
