#!/usr/bin/env python3
"""把 output/raw/*.txt 组装成 output/dialogues_<domain>.json。

    python scripts/parse.py       重新解析已有 raw，不花钱

被 run.py 调用，也能单独跑。全部解析成功后删掉 raw。
"""

import json
import re

import ui
from config import ROOT, load

STRIP = re.compile(r"^\s*(?:[-*•]|\d+[.、)])\s*")


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


def validate(did, msgs, cfg):
    """轮数落在 [min_turns, max_turns] 区间内，角色严格交替，没有空消息。"""
    errs = []
    low, high = cfg["min_turns"], cfg["max_turns"]
    if len(msgs) % 2:
        errs.append(f"{did}: {len(msgs)} 条消息，是单数，最后一条得是 A")
    elif not low * 2 <= len(msgs) <= high * 2:
        errs.append(f"{did}: {len(msgs) // 2} 轮，要 {low} 到 {high} 轮")
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
        ui.panel(ui.warn("解析"), ["output/raw/ 里没有待解析的文件"])
        return []

    points = load_points(cfg)
    by_domain, errs = {}, []

    for f in files:
        # 文件名是 {domain}_{keyword}_{batch}，从右边切两刀，
        # 领域名自己带下划线也不会切错
        domain = f.stem.rsplit("_", 2)[0]
        lookup = points.get(domain, {})
        for did, msgs in parse_raw(f.read_text(encoding="utf-8")):
            e = validate(did, msgs, cfg)
            if e:
                errs += [f"{f.name}  {x}" for x in e]
                continue
            kw, point = lookup.get(did, ("?", "?"))
            by_domain.setdefault(domain, []).append({
                "source": f"{kw}/{did}",
                "point": point,
                "turns": len(msgs) // 2,
                "messages": [{"role": r, "content": c} for r, c in msgs],
            })

    if errs:
        ui.panel(ui.warn(f"解析失败 {len(errs)} 处"),
                 errs[:20]
                 + ([ui.dim(f"另有 {len(errs) - 20} 处")] if len(errs) > 20 else [])
                 + ["", ui.dim("raw 保留在 output/raw/，改完重跑 python scripts/parse.py")])
        return []

    written, rows = [], []
    for domain, dialogues in by_domain.items():
        dialogues.sort(key=lambda d: d["source"])
        out = ROOT / cfg["output_dir"] / f"dialogues_{domain}.json"
        out.write_text(json.dumps({
            "persona": cfg["persona"],
            "domain": domain,
            "profile": cfg["profile"],
            "min_turns": cfg["min_turns"],
            "max_turns": cfg["max_turns"],
            "dialogues": dialogues,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(out)
        counts = {}
        for d in dialogues:
            counts[d["turns"]] = counts.get(d["turns"], 0) + 1
        spread = " · ".join(f"{t} 轮 {n} 段" for t, n in sorted(counts.items()))
        rows.append([out.name, f"{len(dialogues)} 段", spread])

    ui.panel("解析", ui.table(rows, aligns=["left", "right", "left"]))
    for f in files:
        f.unlink()
    return written


if __name__ == "__main__":
    parse_all(load())
