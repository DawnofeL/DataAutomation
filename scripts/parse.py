#!/usr/bin/env python3
"""把 output/raw/*.txt 组装成 JSON，落盘前过一道闸门。

    合格的  →  output/dialogues_<domain>.json
    不合格  →  output/rejected_<domain>.json

闸门两关：结构（轮数、角色交替）和 check.py 的硬失败。两关都过才进
dialogues，任何一关没过就整段进 rejected，带上是哪一条不合格。
dialogues_*.json 里因此永远是 0 硬失败。

    python scripts/parse.py       重新解析已有 raw，不花钱

被 run.py 调用，也能单独跑。全部解析成功后删掉 raw。
"""

import json
import re

import check
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
    """扫 output/raw/，逐段过闸门，合格的和不合格的分别落盘。

    Args:
        cfg (dict): config.load() 的返回值。

    Returns:
        list[Path]: 写出去的文件路径。
    """
    raw_dir = ROOT / cfg["output_dir"] / "raw"
    files = sorted(raw_dir.glob("*.txt"))
    if not files:
        ui.panel(ui.warn("解析"), ["output/raw/ 里没有待解析的文件"])
        return []

    points = load_points(cfg)
    passed, rejected = {}, {}

    for f in files:
        # 文件名是 {domain}_{keyword}_{batch}，从右边切两刀，
        # 领域名自己带下划线也不会切错
        domain = f.stem.rsplit("_", 2)[0]
        lookup = points.get(domain, {})
        for did, msgs in parse_raw(f.read_text(encoding="utf-8")):
            keyword, point = lookup.get(did, ("?", "?"))
            record = {
                "source": f"{keyword}/{did}",
                "point": point,
                "turns": len(msgs) // 2,
                "messages": [{"role": r, "content": c} for r, c in msgs],
            }
            problems = validate(did, msgs, cfg)
            if not problems:
                problems = check.hard_of(record, cfg, cfg["profile"])
            if problems:
                record["problems"] = problems
                rejected.setdefault(domain, []).append(record)
            else:
                passed.setdefault(domain, []).append(record)

    written = []
    written += _write(cfg, passed, "dialogues", ok=True)
    written += _write(cfg, rejected, "rejected", ok=False)
    _report(passed, rejected)

    for f in files:
        f.unlink()
    return written


def _write(cfg, by_domain, prefix, ok):
    """把分流后的一堆对话写成 JSON。

    Args:
        cfg (dict): config.load() 的返回值。
        by_domain (dict[str, list]): {领域: [对话, ...]}。
        prefix (str): 文件名前缀，dialogues 或 rejected。
        ok (bool): 合格的那一堆传 True，不合格传 False。

    Returns:
        list[Path]: 写出去的文件路径。
    """
    out = []
    for domain, items in by_domain.items():
        items.sort(key=lambda d: d["source"])
        path = ROOT / cfg["output_dir"] / f"{prefix}_{domain}.json"
        path.write_text(json.dumps({
            "persona": cfg["persona"],
            "domain": domain,
            "profile": cfg["profile"],
            "min_turns": cfg["min_turns"],
            "max_turns": cfg["max_turns"],
            ("dialogues" if ok else "rejected"): items,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        out.append(path)
    return out


def _report(passed, rejected):
    """把分流结果印成一张面板。

    Args:
        passed (dict[str, list]): 合格的。
        rejected (dict[str, list]): 不合格的。
    """
    rows = []
    for domain in sorted(set(passed) | set(rejected)):
        good, bad = passed.get(domain, []), rejected.get(domain, [])
        counts = {}
        for d in good:
            counts[d["turns"]] = counts.get(d["turns"], 0) + 1
        spread = " · ".join(f"{t} 轮 {n} 段" for t, n in sorted(counts.items()))
        rows.append(f"{ui.key(domain)}  {ui.ok(f'合格 {len(good)} 段')}"
                    f"{ui.bad(f'  打回 {len(bad)} 段') if bad else ''}")
        if spread:
            rows.append(f"  {ui.dim(spread)}")
        for d in bad:
            rows.append(f"  {ui.bad(d['source'])}  {ui.dim(d['problems'][0])}")
    ui.panel("落盘前分流", rows)


if __name__ == "__main__":
    parse_all(load())
