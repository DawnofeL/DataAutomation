"""唯一入口：读讨论点，调模型生成对话，分流落盘，质检。

本文件按运行顺序定义十三个函数：

准备组

- `Check_Config`：开跑前把 `config.yaml` 里会炸的值一次性全挑出来。
- `Build_Tasks`：扫 `input/` 下的讨论点 JSON，按关键词切成一次调用一批。
- `Task_Tag`：把一个任务压成一行标识，打印时用。

开跑前的三张面板

- `Panel_Input`：每个领域几个关键词、多少讨论点、切成几批。
- `Panel_Params`：这一趟用什么模型、什么长度形状、怎么并发。
- `Panel_Estimate`：预估要吞多少 token，账上还剩多少钱。
- `Print_Preview`：`--preview` 专用，把第一批实际会发出去的两条消息原样打出来。

跑

- `Run_One_Batch`：跑一批，调 `llm.Generate_Batch`，把原文写进 `output/raw/`。
- `Generate_All`：并发跑完所有批次，屏幕底部两行常驻。它调下面两个画那两行。
- `_Line_Progress`：常驻区第一行，进度条加刚跑完的那一批。
- `_Line_Usage`：常驻区第二行，累计 token 加重发次数。
- `Panel_Result`：跑完的账，实际用量、余额变化、哪几批没跑成。

总入口

- `Main`：把上面全部串起来，再依次调 `parse.Parse_All` 和 `check.Check_All`。

这个文件只管调度和落盘，不碰提示词也不发 HTTP，那些在 `scripts/llm.py`；
token 和余额在 `scripts/usage.py`；命令行长什么样在 `scripts/ui/`。

    python scripts/run.py            打印预估，等确认后跑
    python scripts/run.py --yes      不问直接跑
    python scripts/run.py --plan     只打印预估和余额，不生成
    python scripts/run.py --preview  打印第一批拼好的 system 和 user，完全离线
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


# ---------- 配置区 ----------

# 项目根目录，也就是 scripts/ 的上一级。
ROOT = Path(__file__).resolve().parent.parent

# 把 scripts/ 塞进模块搜索路径，下面几个同目录模块才 import 得到。
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check as checker  # noqa: E402
import config  # noqa: E402
import llm  # noqa: E402
import parse as parser_  # noqa: E402
import ui  # noqa: E402
import usage as accounting  # noqa: E402


# ---------- 准备 ----------


def Check_Config(cfg: dict) -> list:
    """
    开跑前把 `config.yaml` 里会炸的值一次性全挑出来。

    宁可在这里退出，也不要跑到一半才发现长度档位拼错了。一次把能挑的全列完，
    省得改一条跑一次。

    Args:
        cfg: `config.Load_Config` 的返回值。
    Returns:
        问题清单，一条一行。空列表表示配置没毛病。
    """

    problems = []

    if not cfg.get("api_key"):
        problems.append("没有 api_key。在根目录建 secrets.yaml 写一行 "
                        "api_key: sk-...，或者设环境变量 DEEPSEEK_API_KEY")

    if cfg["profile"] not in cfg["profiles"]:
        problems.append(f"profile 是 {cfg['profile']}，profiles 里没有这一档")

    # profile_max_chars 缺这一档的话，质检取字数上限时会 KeyError
    if cfg["profile"] not in cfg["profile_max_chars"]:
        problems.append(f"profile_max_chars 里没有 {cfg['profile']}，质检会炸")

    # 0 会让下面切批的 range 直接抛 ValueError
    if cfg["dialogue_gen_per_call"] < 1:
        problems.append("dialogue_gen_per_call 至少是 1")

    if cfg["concurrency"] < 1:
        problems.append("concurrency 至少是 1")

    if not 1 <= cfg["min_turns"] <= cfg["max_turns"]:
        problems.append(f"轮数区间不成立：min_turns {cfg['min_turns']}，"
                        f"max_turns {cfg['max_turns']}")

    return problems


def Build_Tasks(cfg: dict) -> tuple:
    """
    扫 `input/` 下所有讨论点 JSON，按关键词切成一次调用一批。

    一次调用只带一个关键词的 `dialogue_gen_per_call` 条讨论点，上下文干净。
    立场（opinion）空着的文件整个跳过，没有立场生成出来的对话没有观点。

    Args:
        cfg: `config.Load_Config` 的返回值。
    Returns:
        `(任务列表, 跳过原因列表)`。每个任务是一个字典，含
        domain、keyword、batch、points、opinion 五个键。
    """

    task_list = []
    skipped_list = []
    batch_size = cfg["dialogue_gen_per_call"]

    for one_path in sorted((ROOT / cfg["input_dir"]).glob("*.json")):
        input_data = json.loads(one_path.read_text(encoding = "utf-8"))
        opinion_text = input_data.get("opinion", "").strip()

        if not opinion_text:
            skipped_list.append(f"{one_path.name}：opinion 空着")
            continue

        for one_topic in input_data["topics"]:
            point_list = one_topic["discussions"]

            # 按 batch_size 步长切片，最后一批不够也照切，比如 5 条切成 4 + 1
            for start_index in range(0, len(point_list), batch_size):
                task_list.append({
                    "domain": input_data["domain"],
                    "keyword": one_topic["keyword"],
                    "batch": start_index // batch_size + 1,
                    "points": point_list[start_index:start_index + batch_size],
                    "opinion": opinion_text,
                })

    return task_list, skipped_list


def Task_Tag(task: dict) -> str:
    """
    把一个任务压成一行标识，打印时用。

    Args:
        task: `Build_Tasks` 里的一项。
    Returns:
        形如「彩礼 第2批」。
    """

    return f"{task['keyword']} 第{task['batch']}批"


# ---------- 开跑前的面板 ----------


def Panel_Input(task_list: list, skipped_list: list) -> None:
    """
    输入面板：每个领域几个关键词、多少讨论点、切成几批。

    Args:
        task_list: `Build_Tasks` 的任务列表。
        skipped_list: `Build_Tasks` 的跳过原因列表。
    """

    # 按领域再按关键词攒两个数：这个关键词下有几条讨论点、切成了几批
    by_domain = {}
    for one_task in task_list:
        entry = by_domain.setdefault(one_task["domain"], {}).setdefault(one_task["keyword"], [0, 0])
        entry[0] += len(one_task["points"])
        entry[1] += 1

    panel_rows = []
    for domain_name, keyword_map in by_domain.items():
        point_total = sum(one[0] for one in keyword_map.values())
        panel_rows.append(
            f"{ui.Key(domain_name)}  "
            f"{ui.Dim(f'{len(keyword_map)} 个关键词 · {point_total} 条讨论点')}"
        )
        panel_rows += ["  " + one_line for one_line in ui.Table(
            [[kw, f"{n} 条", "→", f"{c} 批"] for kw, (n, c) in keyword_map.items()],
            aligns = ["left", "right", "left", "right"])]

    for one_skip in skipped_list:
        panel_rows.append(ui.Warn(f"跳过  {one_skip}"))

    ui.Panel("输入", panel_rows)


def Panel_Params(cfg: dict) -> None:
    """
    参数面板：这一趟用什么模型、什么长度形状、轮数区间、怎么并发。

    Args:
        cfg: `config.Load_Config` 的返回值。
    """

    ui.Panel("参数", [
        ui.Key_Value("模型", cfg["model"], f"思考 {'开' if cfg['thinking'] else '关'}"),
        ui.Key_Value("长度", cfg["profile"], cfg["profiles"][cfg["profile"]]),
        ui.Key_Value("轮数", f"{cfg['min_turns']} 到 {cfg['max_turns']} 轮", "由讨论点自己定，不硬凑"),
        ui.Key_Value("并发", f"{cfg['concurrency']} 路",
                     f"每批 {cfg['dialogue_gen_per_call']} 段 · 不合格重发 {cfg['max_retry']} 次"),
    ])


def Panel_Estimate(cfg: dict, system_text: str, user_texts: list,
                   dialogue_count: int, balance) -> None:
    """
    预估面板：这一趟大概吞多少 token，账上还剩多少。

    Args:
        cfg: `config.Load_Config` 的返回值。
        system_text: `llm.Build_System` 的结果。
        user_texts: 每次调用的 user 消息。
        dialogue_count: 这一趟要产出几段对话。
        balance: `usage.Query_Balance` 的返回值，查不到时是 None。
    """

    estimated = accounting.Estimate_Usage(cfg, system_text, user_texts, dialogue_count)
    panel_rows = ui.Table(accounting.Usage_Rows(estimated), aligns = ["left", "right"])

    panel_rows.append("")
    panel_rows.append(ui.Dim("输入真数，输出按每条 25 字拍"))

    if balance:
        panel_rows.append(ui.Key_Value("余额", f"{balance[0]} {balance[1]}", label_cells = 12))

    ui.Panel(f"预估  {len(user_texts)} 次调用 → {dialogue_count} 段对话", panel_rows)


def Print_Preview(cfg: dict, task_list: list, system_text: str) -> None:
    """
    把第一批实际会发出去的 system 和 user 原样打出来，完全离线不发请求。

    Args:
        cfg: `config.Load_Config` 的返回值。
        task_list: `Build_Tasks` 的任务列表。
        system_text: `llm.Build_System` 的结果。
    """

    first_task = task_list[0]
    user_text = llm.Build_User(cfg, first_task["domain"], first_task["keyword"],
                               first_task["opinion"], first_task["points"])

    for name_text, body_text in (("system", system_text), ("user", user_text)):
        ui.Banner(f"{name_text}   {len(body_text)} 字 · {accounting.Count_Tokens(body_text)} token")
        ui.Rule()
        print(body_text)
        ui.Rule()

    ui.Banner(f"以上是「{Task_Tag(first_task)}」的两条消息，共 {len(task_list)} 批，system 每批相同。")
    print()


# ---------- 跑 ----------


def Run_One_Batch(cfg: dict, system_text: str, task: dict) -> tuple:
    """
    跑一批：调模型，把原文写进 `output/raw/`。

    重发用光了照样写。里面往往只有一两段不合格，其余能救，整批丢掉等于连累旁边
    那几段。合格不合格由落盘前那道闸门逐段判，这里不拦。

    Args:
        cfg: `config.Load_Config` 的返回值。
        system_text: `llm.Build_System` 的结果。
        task: `Build_Tasks` 里的一项。
    Returns:
        `(任务, 每次请求的官方 usage 列表, 错在哪)`。成功时第三项是 None。
    """

    raw_text, usage_list, error_text = llm.Generate_Batch(
        cfg, system_text, task["domain"], task["keyword"], task["opinion"], task["points"])

    # 文件名带上领域、关键词、第几批，解析时从右边切两刀就能取回领域名
    file_name = f"{task['domain']}_{task['keyword']}_{task['batch']}.txt"
    out_path = ROOT / cfg["output_dir"] / "raw" / file_name
    out_path.parent.mkdir(parents = True, exist_ok = True)

    # 空串说明连一次成功响应都没拿到，写个空文件没意义
    if raw_text:
        out_path.write_text(raw_text, encoding = "utf-8")

    return task, usage_list, error_text


def Generate_All(cfg: dict, system_text: str, task_list: list) -> tuple:
    """
    并发跑完所有批次，屏幕底部两行常驻：进度条加累计用量。

    每收到一批就把它的官方 usage 累加进去重画，跑的过程中随时能看到已经烧了
    多少 token，不用等跑完。

    Args:
        cfg: `config.Load_Config` 的返回值，用到 concurrency。
        system_text: `llm.Build_System` 的结果。
        task_list: `Build_Tasks` 的任务列表。
    Returns:
        `(收集到的官方 usage 列表, 失败清单)`。失败清单每项是 `(批次名, 错在哪)`。
    """

    print()
    live_block = ui.Live(2)
    collected_usage = []
    fail_list = []
    retry_count = 0
    done_count = 0

    live_block.Update([_Line_Progress(0, len(task_list), 0, "等第一批返回"),
                       _Line_Usage(collected_usage, 0)])

    with ThreadPoolExecutor(max_workers = cfg["concurrency"]) as pool:
        future_list = [pool.submit(Run_One_Batch, cfg, system_text, one) for one in task_list]

        # as_completed 谁先跑完先返回谁，不按提交顺序，所以进度条上的批次名是乱序的
        for one_future in as_completed(future_list):
            one_task, usage_list, error_text = one_future.result()
            collected_usage += usage_list

            # 一批发了 n 次请求就意味着重发了 n-1 次，第一次不算重发
            retry_count += max(0, len(usage_list) - 1)
            done_count += 1

            if error_text:
                fail_list.append((Task_Tag(one_task), error_text))

            live_block.Update([
                _Line_Progress(done_count, len(task_list), len(fail_list),
                               Task_Tag(one_task), error_text),
                _Line_Usage(collected_usage, retry_count),
            ])

    live_block.Close()
    return collected_usage, fail_list


def _Line_Progress(done: int, total: int, fail_count: int, note: str, error = None) -> str:
    """
    常驻区第一行：进度条加计数加刚跑完的那一批。

    Args:
        done: 已完成批数。
        total: 总批数。
        fail_count: 到目前为止有几批失败。
        note: 刚跑完的批次名，或者一句等待提示。
        error: 这一批的错，非 None 就把标记涂红。
    Returns:
        排好的一行。
    """

    mark_text = ui.Bad(ui.MARK_BAD) if error else ui.Ok(ui.MARK_OK)

    # 一批都还没跑完时不画勾也不画叉，只用灰字提示在等
    tail_text = f"{mark_text} {note}" if done else ui.Dim(note)
    fail_text = ui.Bad(f"  失败 {fail_count}") if fail_count else ""

    return (f"生成  {ui.Draw_Bar(done, total)}  "
            f"{ui.Pad_To_Width(f'{done}/{total}', 7)}{tail_text}{fail_text}")


def _Line_Usage(collected_usage: list, retry_count: int) -> str:
    """
    常驻区第二行：到目前为止烧掉的 token，以及重发了几次。

    Args:
        collected_usage: 已经收到的官方 usage 列表。
        retry_count: 到目前为止重发了几次。
    Returns:
        排好的一行。
    """

    body_text = accounting.Format_Usage_Line(accounting.Merge_Usage(collected_usage))
    tail_text = ui.Warn(f"  重发 {retry_count}") if retry_count else ""

    return f"{ui.Dim('用量')}  {body_text}{tail_text}"


def Panel_Result(cfg: dict, collected_usage: list, fail_list: list, before) -> None:
    """
    跑完的账：实际吞了多少 token，余额动了多少，哪几批重发用光还不合格。

    Args:
        cfg: `config.Load_Config` 的返回值。
        collected_usage: 每次请求的官方 usage 列表。
        fail_list: 失败清单，每项是 `(批次名, 错在哪)`。
        before: 跑之前 `usage.Query_Balance` 的返回值。
    """

    panel_rows = ui.Table(accounting.Usage_Rows(accounting.Merge_Usage(collected_usage)),
                          aligns = ["left", "right"])
    panel_rows.append("")
    panel_rows.append(ui.Key_Value(
        "余额",
        accounting.Format_Balance(before, accounting.Query_Balance(cfg)),
        label_cells = 12,
    ))

    ui.Panel("实际", panel_rows)

    if fail_list:
        ui.Panel(ui.Warn(f"{len(fail_list)} 批重发用光还不合格，逐段分流"),
                 ui.Table([[name_text, why_text] for name_text, why_text in fail_list]))


def Main() -> None:
    """入口：查配置 → 打三张面板 → 等确认 → 并发生成 → 分流落盘 → 质检。"""

    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument("--yes", action = "store_true", help = "不问直接跑")
    arg_parser.add_argument("--plan", action = "store_true", help = "只打印预估和余额，不生成")
    arg_parser.add_argument("--preview", action = "store_true", help = "只打印拼好的 system 和 user")
    args = arg_parser.parse_args()

    cfg = config.Load_Config()
    config_problems = Check_Config(cfg)
    if config_problems:
        sys.exit("\n".join(config_problems))

    # system 跟具体讨论点无关，开跑前拼一次，所有批次共用同一份
    system_text = llm.Build_System(cfg)

    task_list, skipped_list = Build_Tasks(cfg)
    if not task_list:
        sys.exit(f"{cfg['input_dir']} 里没有可用的讨论点 JSON")

    if args.preview:
        Print_Preview(cfg, task_list, system_text)
        return

    user_texts = [
        llm.Build_User(cfg, one["domain"], one["keyword"], one["opinion"], one["points"])
        for one in task_list
    ]
    dialogue_count = sum(len(one["points"]) for one in task_list)
    balance_before = accounting.Query_Balance(cfg)

    ui.Banner("DataAutomation · 讨论点 → 多轮对话")
    Panel_Input(task_list, skipped_list)
    Panel_Params(cfg)
    Panel_Estimate(cfg, system_text, user_texts, dialogue_count, balance_before)

    if args.plan:
        print()
        return

    if not args.yes and input(f"\n{ui.INDENT}继续？[y/N] ").strip().lower() != "y":
        return

    collected_usage, fail_list = Generate_All(cfg, system_text, task_list)
    Panel_Result(cfg, collected_usage, fail_list, balance_before)

    # 分流和质检都自己印面板，这里只负责按顺序调
    written_paths = parser_.Parse_All(cfg)
    checker.Check_All(cfg)

    if written_paths:
        ui.Panel("落盘", [ui.Ok(f"{ui.MARK_OK} {one.relative_to(ROOT)}") for one in written_paths])

    print()


if __name__ == "__main__":
    Main()
