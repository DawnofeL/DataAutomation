#!/usr/bin/env python3
"""把 output/raw/*.txt 组装成 output/dialogues_<domain>.json。

    python scripts/parse.py       重新解析已有 raw，不花钱

被 run.py 调用，也能单独跑。全部解析成功后删掉 raw。
"""

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STRIP = re.compile(r"^\s*(?:[-*•]|\d+[.、)])\s*")


def load_config():
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def load_points(cfg):
    """{domain: {discussion_id: (keyword, point)}}"""
    out = {}
    for path in sorted((ROOT / cfg["input_dir"]).glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        m = out.setdefault(data["domain"], {})
        for topic in data["topics"]:
            for d in topic["discussions"]:
                m[d["id"]] = (topic["keyword"], d["point"])
    return out


def parse_raw(text):
    """返回 [(discussion_id, [(role, content), ...]), ...]"""
    blocks, cur = [], None
    for line in text.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        if line.startswith("==="):
            cur = (line.lstrip("= ").strip(), [])
            blocks.append(cur)
            continue
        if cur is None:
            continue
        body = STRIP.sub("", line)
        if body[:2] in ("U ", "A "):
            cur[1].append(("user" if body[0] == "U" else "assistant", body[2:].strip()))
        elif cur[1]:
            # 模型手滑换了行，接到上一条后面
            role, content = cur[1][-1]
            cur[1][-1] = (role, content + body)
    return blocks


def validate(did, msgs, turns):
    errs = []
    if len(msgs) != turns * 2:
        errs.append(f"{did}: {len(msgs)} 条消息，应为 {turns*2} 条（{turns} 轮）")
    for i, (role, content) in enumerate(msgs):
        want = "user" if i % 2 == 0 else "assistant"
        if role != want:
            errs.append(f"{did}: 第 {i+1} 条应该是 {want}，实际是 {role}")
            break
        if not content:
            errs.append(f"{did}: 第 {i+1} 条是空的")
    return errs


def parse_all(cfg):
    raw_dir = ROOT / cfg["output_dir"] / "raw"
    files = sorted(raw_dir.glob("*.txt"))
    if not files:
        print("output/raw/ 里没有待解析的文件", file=sys.stderr)
        return []

    points = load_points(cfg)
    by_domain, errs = {}, []

    for f in files:
        domain = f.stem.split("_")[0]
        lookup = points.get(domain, {})
        for did, msgs in parse_raw(f.read_text(encoding="utf-8")):
            e = validate(did, msgs, cfg["turns"])
            if e:
                errs += [f"{f.name}  {x}" for x in e]
                continue
            kw, point = lookup.get(did, ("?", "?"))
            by_domain.setdefault(domain, []).append({
                "source": f"{kw}/{did}",
                "point": point,
                "messages": [{"role": r, "content": c} for r, c in msgs],
            })

    if errs:
        print("解析失败：", file=sys.stderr)
        for e in errs:
            print(f"  {e}", file=sys.stderr)
        print("raw 保留，改完重跑 parse.py", file=sys.stderr)
        return []

    written = []
    for domain, dialogues in by_domain.items():
        dialogues.sort(key=lambda d: d["source"])
        out = ROOT / cfg["output_dir"] / f"dialogues_{domain}.json"
        out.write_text(json.dumps({
            "persona": cfg["persona"],
            "domain": domain,
            "profile": cfg["profile"],
            "turns": cfg["turns"],
            "dialogues": dialogues,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(out)
        print(f"解析 {len(dialogues)} 段  →  {out.name}")

    for f in files:
        f.unlink()
    return written


if __name__ == "__main__":
    parse_all(load_config())
