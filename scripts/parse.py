"""把模型吐的纯文本组装成 JSON，落盘前过一道闸门。

本文件按调用顺序定义五个函数：

- `Load_Points`：从 `input/` 读出讨论点原文，建一张查询表，落盘时把原文写回每段对话。
- `Parse_Raw`：把一份 raw 文本按 `===` 切成一段段对话，读出每条消息的角色。
- `Validate_Structure`：查一段对话的结构，轮数落没落在区间里、角色有没有交替。
- `Parse_All`：总入口。扫 `output/raw/`，逐段过闸门，合格和不合格分开落盘。
  它内部调上面三个，再调下面两个收尾。
- `_Write_Json`：把分流后的一堆对话写成 JSON 文件。
- `_Report_Split`：把分流结果印成一张面板。

闸门有两关：结构关走本文件的 `Validate_Structure`，内容关走
`check.Hard_Problems_Of`（质检脚本里那套禁词、问句结尾之类的硬规则）。两关都过
才进 `dialogues_<领域>.json`，任何一关没过就整段进 `rejected_<领域>.json` 并带上
是哪一条不合格。所以 `dialogues_*.json` 里永远是 0 硬失败。

`llm.py` 判断要不要重发时用的也是这两关，标准完全一致。

重新解析一遍已有的 raw，不花钱：

    python scripts/parse.py
"""

import json
import re

import check
import ui
from config import ROOT, Load_Config


# ---------- 配置区 ----------

# 模型偶尔会在 U / A 前面多打一个列表符号或序号，解析前先剥掉。
LIST_MARKER_PATTERN = re.compile(r"^\s*(?:[-*•]|\d+[.、)])\s*")


def Load_Points(cfg: dict) -> dict:
    """
    从 `input/` 下所有讨论点 JSON 里建一张查询表。

    模型返回的原文里只有讨论点编号（形如 `1-1`），没有讨论点原文。落盘时要把
    原文和它所属的关键词写回每段对话，方便日后追溯是哪一条讨论点产出的，
    所以先建这张表备查。

    Args:
        cfg: `config.Load_Config` 的返回值，用到 input_dir。
    Returns:
        形如 `{领域: {讨论点编号: (关键词, 讨论点原文)}}` 的嵌套字典。
    """

    lookup_table = {}
    for one_path in sorted((ROOT / cfg["input_dir"]).glob("*.json")):
        input_data = json.loads(one_path.read_text(encoding = "utf-8"))
        domain_map = lookup_table.setdefault(input_data["domain"], {})

        # 一个领域下面挂若干关键词，一个关键词下面挂若干讨论点，摊平成编号到内容的映射
        for one_topic in input_data["topics"]:
            for one_point in one_topic["discussions"]:
                domain_map[one_point["id"]] = (one_topic["keyword"], one_point["point"])

    return lookup_table


def Parse_Raw(text: str) -> list:
    """
    把一份 raw 文本切成一段段对话。

    模型按约定输出纯文本：`=== 讨论点编号` 起一段，`U ` 开头是用户说的，
    `A ` 开头是 assistant 说的。这里只管拆，不判断内容好坏。

    两处容错：行首多出来的列表符号和序号先剥掉；模型手滑把一条消息断成两行时，
    没有前缀的那一行接到上一条后面，不新起一条。

    Args:
        text: 模型返回的原文。
    Returns:
        形如 `[(讨论点编号, [(角色, 内容), ...]), ...]` 的列表。
    """

    blocks = []
    current_block = None

    for one_line in text.splitlines():
        one_line = one_line.rstrip()

        if not one_line.strip():
            continue

        # === 开头是新一段的分界，后面跟的是讨论点编号
        if one_line.startswith("==="):
            current_block = (one_line.lstrip("= ").strip(), [])
            blocks.append(current_block)
            continue

        # 第一个 === 还没出现就有正文，说明是模型多说的开场白，丢掉
        if current_block is None:
            continue

        body_text = LIST_MARKER_PATTERN.sub("", one_line)

        # U 和 A 加一个空格是角色前缀，剩下的才是这条消息的正文
        if body_text[:2] in ("U ", "A "):
            role_name = "user" if body_text[0] == "U" else "assistant"
            current_block[1].append((role_name, body_text[2:].strip()))

        elif current_block[1]:

            # 没有前缀说明这一行是上一条消息被断开的后半截，直接接回去
            last_role, last_content = current_block[1][-1]
            current_block[1][-1] = (last_role, last_content + body_text)

    return blocks


def Validate_Structure(discussion_id: str, messages: list, cfg: dict) -> list:
    """
    查一段对话的结构对不对。

    三件事：消息总数是偶数（最后一条必须是 assistant），轮数落在
    `[min_turns, max_turns]` 区间里，角色严格一问一答交替且没有空消息。
    内容写得好不好不在这里管，那是 `check.py` 的事。

    Args:
        discussion_id: 讨论点编号，只用来拼报错信息。
        messages: `Parse_Raw` 切出来的 `[(角色, 内容), ...]`。
        cfg: `config.Load_Config` 的返回值，用到 min_turns 和 max_turns。
    Returns:
        毛病清单，一条一行。空列表表示结构没问题。
    """

    problems = []
    low_turns = cfg["min_turns"]
    high_turns = cfg["max_turns"]

    # 单数说明最后一条是 user，对话没收尾，这种直接判废不再往下细查
    if len(messages) % 2:
        problems.append(f"{discussion_id}: {len(messages)} 条消息，是单数，最后一条得是 A")

    elif not low_turns * 2 <= len(messages) <= high_turns * 2:
        problems.append(f"{discussion_id}: {len(messages) // 2} 轮，要 {low_turns} 到 {high_turns} 轮")

    for message_index, (role_name, content_text) in enumerate(messages):

        # 偶数位必须是 user，奇数位必须是 assistant，错一处后面全乱，报一条就够
        want_role = "user" if message_index % 2 == 0 else "assistant"
        if role_name != want_role:
            problems.append(f"{discussion_id}: 第 {message_index + 1} 条应该是 {want_role}，实际是 {role_name}")
            break

        if not content_text:
            problems.append(f"{discussion_id}: 第 {message_index + 1} 条是空的")

    return problems


def Parse_All(cfg: dict) -> list:
    """
    扫 `output/raw/` 下所有原文，逐段过闸门，合格和不合格分开落盘。

    一段不合格只影响它自己，同一批里其余的照常落盘，不会因为一段坏掉就整批丢掉。

    同一个讨论点可能在一份 raw 里出现两遍（模型重发之后把两个版本都吐出来了），
    这里按编号先全收着，最后每个编号只留一份，能过闸门的那份优先。

    Args:
        cfg: `config.Load_Config` 的返回值。
    Returns:
        写出去的文件路径列表。
    """

    raw_dir = ROOT / cfg["output_dir"] / "raw"
    raw_files = sorted(raw_dir.glob("*.txt"))

    if not raw_files:
        ui.Panel(ui.Warn("解析"), ["output/raw/ 里没有待解析的文件"])
        return []

    point_lookup = Load_Points(cfg)

    # 键是 (领域, 讨论点编号)，值是那一段对话，重复出现时按下面的规则挑一份留下
    collected = {}

    for one_file in raw_files:

        # 文件名是 {领域}_{关键词}_{第几批}.txt，从右边切两刀取领域名，领域名自己带下划线也不会切错
        domain_name = one_file.stem.rsplit("_", 2)[0]
        domain_lookup = point_lookup.get(domain_name, {})

        for discussion_id, messages in Parse_Raw(one_file.read_text(encoding = "utf-8")):
            keyword_name, point_text = domain_lookup.get(discussion_id, ("?", "?"))

            one_record = {
                "source": f"{keyword_name}/{discussion_id}",
                "point": point_text,
                "turns": len(messages) // 2,
                "messages": [{"role": r, "content": c} for r, c in messages],
            }

            # 先过结构关，结构没问题才值得往下查内容，结构坏了内容查了也没意义
            problems = Validate_Structure(discussion_id, messages, cfg)
            if not problems:
                problems = check.Hard_Problems_Of(one_record, cfg, cfg["profile"])

            if problems:
                one_record["problems"] = problems

            # 已经收过一份合格的就不换了，收过的那份不合格则让位给这一份
            old_record = collected.get((domain_name, discussion_id))
            if old_record is None or ("problems" in old_record and "problems" not in one_record):
                collected[(domain_name, discussion_id)] = one_record

    # 收完再按有没有 problems 这个键分成两堆，带这个键的就是没过闸门的
    passed_by_domain = {}
    rejected_by_domain = {}
    for (domain_name, _), one_record in collected.items():
        if "problems" in one_record:
            rejected_by_domain.setdefault(domain_name, []).append(one_record)
        else:
            passed_by_domain.setdefault(domain_name, []).append(one_record)

    written_paths = []
    written_paths += _Write_Json(cfg, passed_by_domain, "dialogues", is_passed = True)
    written_paths += _Write_Json(cfg, rejected_by_domain, "rejected", is_passed = False)
    _Report_Split(passed_by_domain, rejected_by_domain)

    # raw 已经全部转成 JSON 了，留着只会让下次运行重复解析一遍
    for one_file in raw_files:
        one_file.unlink()

    return written_paths


def _Write_Json(cfg: dict, by_domain: dict, prefix: str, is_passed: bool) -> list:
    """
    把分流后的一堆对话写成 JSON 文件，一个领域一个文件。

    人设、长度档位、轮数区间这些整批共用的信息记在顶层，不逐条重复。每段自己的
    轮数逐条记，因为轮数每段都不一样。

    Args:
        cfg: `config.Load_Config` 的返回值。
        by_domain: 形如 `{领域: [对话, ...]}`。
        prefix: 文件名前缀，合格的传 "dialogues"，打回的传 "rejected"。
        is_passed: 合格的那一堆传 True，不合格传 False，决定 JSON 里那个数组叫什么。
    Returns:
        写出去的文件路径列表。
    """

    written_paths = []
    for domain_name, record_list in by_domain.items():

        # 按 source 排序，同一批数据两次跑出来顺序一致，方便 diff
        record_list.sort(key = lambda one: one["source"])

        out_path = ROOT / cfg["output_dir"] / f"{prefix}_{domain_name}.json"
        out_path.write_text(json.dumps({
            "persona": cfg["persona"],
            "domain": domain_name,
            "profile": cfg["profile"],
            "min_turns": cfg["min_turns"],
            "max_turns": cfg["max_turns"],
            ("dialogues" if is_passed else "rejected"): record_list,
        }, ensure_ascii = False, indent = 2) + "\n", encoding = "utf-8")

        written_paths.append(out_path)

    return written_paths


def _Report_Split(passed_by_domain: dict, rejected_by_domain: dict) -> None:
    """
    把分流结果印成一张面板：每个领域合格几段、打回几段、打回的分别栽在哪一条。

    Args:
        passed_by_domain: 过了闸门的，形如 `{领域: [对话, ...]}`。
        rejected_by_domain: 被打回的，形状同上，每段多一个 problems 键。
    """

    panel_rows = []
    for domain_name in sorted(set(passed_by_domain) | set(rejected_by_domain)):
        good_list = passed_by_domain.get(domain_name, [])
        bad_list = rejected_by_domain.get(domain_name, [])

        # 数一下合格的那些各是几轮，摆出来能看出长度有没有拉开差距
        turn_counts = {}
        for one_record in good_list:
            turn_counts[one_record["turns"]] = turn_counts.get(one_record["turns"], 0) + 1
        spread_text = " · ".join(f"{t} 轮 {n} 段" for t, n in sorted(turn_counts.items()))

        bad_text = ui.Bad(f"  打回 {len(bad_list)} 段") if bad_list else ""
        panel_rows.append(f"{ui.Key(domain_name)}  {ui.Ok(f'合格 {len(good_list)} 段')}{bad_text}")

        if spread_text:
            panel_rows.append(f"  {ui.Dim(spread_text)}")

        # 每段只印第一条毛病，全印出来面板会被刷屏，详细的在 rejected JSON 里
        for one_record in bad_list:
            panel_rows.append(f"  {ui.Bad(one_record['source'])}  {ui.Dim(one_record['problems'][0])}")

    ui.Panel("落盘前分流", panel_rows)


if __name__ == "__main__":
    Parse_All(Load_Config())
