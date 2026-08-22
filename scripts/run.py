#!/usr/bin/env python3
"""唯一入口：读 input/ 的讨论点，调模型生成对话，解析、质检、落盘。

    python scripts/run.py            打印预估，等确认后跑
    python scripts/run.py --yes      不问直接跑
    python scripts/run.py --plan     只打印预估和余额，不生成
    python scripts/run.py --preview  打印第一批拼出来的 system 和 user，完全离线

这个文件只管调度和落盘。提示词和 HTTP 在 scripts/llm.py，token 和余额在
scripts/usage.py，命令行长什么样在 scripts/ui/。
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check as checker  # noqa: E402
import config  # noqa: E402
import llm  # noqa: E402
import parse as parser_  # noqa: E402
import ui  # noqa: E402
import usage as accounting  # noqa: E402


def build_tasks(cfg):
    """扫 input/ 下所有 JSON，按 keyword 切成一次调用一批。

    一次调用只带一个 keyword 的 dialogue_gen_per_call 条讨论点，上下文干净。
    opinion 空着的文件整个跳过。

    Args:
        cfg (dict): config.load() 的返回值。

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
        str: 形如「彩礼 第2批」。
    """
    return f"{task['keyword']} 第{task['batch']}批"


# ====================================================================
#  开跑前的三张面板
# ====================================================================


def panel_input(tasks, skipped):
    """输入面板：每个领域几个关键词、多少讨论点、切成几批。

    Args:
        tasks (list[dict]): build_tasks() 的任务列表。
        skipped (list[str]): build_tasks() 的跳过原因。
    """
    by_domain = {}
    for t in tasks:
        entry = by_domain.setdefault(t["domain"], {}).setdefault(t["keyword"], [0, 0])
        entry[0] += len(t["points"])
        entry[1] += 1

    rows = []
    for domain, keywords in by_domain.items():
        points = sum(v[0] for v in keywords.values())
        rows.append(f"{ui.key(domain)}  {ui.dim(f'{len(keywords)} 个关键词 · {points} 条讨论点')}")
        rows += ["  " + line for line in ui.table(
            [[kw, f"{n} 条", "→", f"{calls} 批"] for kw, (n, calls) in keywords.items()],
            aligns=["left", "right", "left", "right"])]
    for s in skipped:
        rows.append(ui.warn(f"跳过  {s}"))
    ui.panel("输入", rows)


def panel_params(cfg):
    """参数面板：这一趟用什么模型、什么形状、怎么并发。

    Args:
        cfg (dict): config.load() 的返回值。
    """
    ui.panel("参数", [
        ui.kv("模型", cfg["model"], f"思考 {'开' if cfg['thinking'] else '关'}"),
        ui.kv("长度", cfg["profile"], cfg["profiles"][cfg["profile"]]),
        ui.kv("轮数", f"{cfg['min_turns']} 到 {cfg['max_turns']} 轮", "由讨论点自己定，不硬凑"),
        ui.kv("并发", f"{cfg['concurrency']} 路",
              f"每批 {cfg['dialogue_gen_per_call']} 段 · 不合格重发 {cfg['max_retry']} 次"),
    ])


def panel_estimate(cfg, system, users, n_dialogues, balance):
    """预估面板：这一趟大概吞多少 token，账上还剩多少。

    Args:
        cfg (dict): config.load() 的返回值。
        system (str): llm.build_system() 的结果。
        users (list[str]): 每次调用的 user 消息。
        n_dialogues (int): 这一趟要产出几段对话。
        balance (tuple | None): usage.balance() 的返回值。
    """
    rows = ui.table(accounting.rows(accounting.estimate(cfg, system, users, n_dialogues)),
                    aligns=["left", "right"])
    rows.append("")
    rows.append(ui.dim("输入真数，输出按每条 25 字拍"))
    if balance:
        rows.append(ui.kv("余额", f"{balance[0]} {balance[1]}", label_cells=12))
    ui.panel(f"预估  {len(users)} 次调用 → {n_dialogues} 段对话", rows)


def print_preview(cfg, tasks, system):
    """把第一批实际会发出去的 system 和 user 原样打出来。

    Args:
        cfg (dict): config.load() 的返回值。
        tasks (list[dict]): build_tasks() 的任务列表。
        system (str): llm.build_system() 的结果。
    """
    t = tasks[0]
    user = llm.build_user(cfg, t["domain"], t["keyword"], t["opinion"], t["points"])
    for name, text in (("system", system), ("user", user)):
        ui.banner(f"{name}   {len(text)} 字 · {accounting.count(text)} token")
        ui.rule()
        print(text)
        ui.rule()
    ui.banner(f"以上是「{tag(t)}」的两条消息，共 {len(tasks)} 批，system 每批相同。")
    print()


# ====================================================================
#  跑
# ====================================================================


def run_one(cfg, system, task):
    """跑一批：调模型，成功就把原文写进 output/raw/。

    Args:
        cfg (dict): config.load() 的返回值。
        system (str): llm.build_system() 的结果。
        task (dict): build_tasks() 里的一项。

    Returns:
        tuple[dict, list[dict], str | None]: (任务, 每次请求的官方 usage, 错在哪)。
            成功时第三项是 None。
    """
    text, usages, error = llm.generate(
        cfg, system, task["domain"], task["keyword"], task["opinion"], task["points"])

    # 重发用光了也把原文写下来。里面往往只有一两段不合格，其余能救；
    # 整批丢掉等于连累旁边那几段。落盘前那道闸门会逐段分流。
    name = f"{task['domain']}_{task['keyword']}_{task['batch']}.txt"
    out = ROOT / cfg["output_dir"] / "raw" / name
    out.parent.mkdir(parents=True, exist_ok=True)
    if text:
        out.write_text(text, encoding="utf-8")
    return task, usages, error


def generate_all(cfg, system, tasks):
    """并发跑完所有批次，屏幕底部两行常驻：进度条 + 累计用量。

    每收到一批就把它的官方 usage 累加进去重画，跑的过程中随时能看到已经
    烧了多少 token，不用等跑完。

    Args:
        cfg (dict): config.load() 的返回值。
        system (str): llm.build_system() 的结果。
        tasks (list[dict]): build_tasks() 的任务列表。

    Returns:
        tuple[list[dict], list[tuple[str, str]]]: (收集到的官方 usage, 失败清单)。
            失败清单每项是 (批次名, 错在哪)。
    """
    print()
    live = ui.Live(2)
    collected, fails, retries = [], [], 0
    done = 0

    live.update([_line_progress(0, len(tasks), 0, "等第一批返回"),
                 _line_usage(collected, 0)])

    with ThreadPoolExecutor(max_workers=cfg["concurrency"]) as pool:
        futures = [pool.submit(run_one, cfg, system, t) for t in tasks]
        for future in as_completed(futures):
            task, usages, error = future.result()
            collected += usages
            retries += max(0, len(usages) - 1)
            done += 1
            if error:
                fails.append((tag(task), error))
            live.update([_line_progress(done, len(tasks), len(fails), tag(task), error),
                         _line_usage(collected, retries)])

    live.close()
    return collected, fails


def _line_progress(done, total, n_fail, note, error=None):
    """常驻区第一行：进度条 + 计数 + 刚跑完的那一批。

    Args:
        done (int): 已完成批数。
        total (int): 总批数。
        n_fail (int): 到目前为止失败几批。
        note (str): 刚跑完的批次名，或者一句等待提示。
        error (str | None): 这一批的错，有就标红。

    Returns:
        str: 排好的一行。
    """
    mark = ui.bad(ui.BAD) if error else ui.ok(ui.OK)
    tail = f"{mark} {note}" if done else ui.dim(note)
    fail_note = ui.bad(f"  失败 {n_fail}") if n_fail else ""
    return (f"生成  {ui.bar(done, total)}  "
            f"{ui.pad(f'{done}/{total}', 7)}{tail}{fail_note}")


def _line_usage(collected, retries):
    """常驻区第二行：到目前为止烧掉的 token。

    Args:
        collected (list[dict]): 已经收到的官方 usage。
        retries (int): 到目前为止重发了几次。

    Returns:
        str: 排好的一行。
    """
    body = accounting.line(accounting.merge(collected))
    tail = ui.warn(f"  重发 {retries}") if retries else ""
    return f"{ui.dim('用量')}  {body}{tail}"


def panel_result(cfg, collected, fails, before):
    """跑完的账：实际吞了多少 token，余额动了多少，哪几批没成。

    Args:
        cfg (dict): config.load() 的返回值。
        collected (list[dict]): 每次请求的官方 usage。
        fails (list[tuple[str, str]]): 失败清单。
        before (tuple | None): 跑之前的余额。
    """
    rows = ui.table(accounting.rows(accounting.merge(collected)), aligns=["left", "right"])
    rows.append("")
    rows.append(ui.kv("余额", accounting.fmt_balance(before, accounting.balance(cfg)),
                      label_cells=12))
    ui.panel("实际", rows)

    if fails:
        ui.panel(ui.warn(f"{len(fails)} 批重发用光还不合格，逐段分流"),
                 ui.table([[name, why] for name, why in fails]))


def check_config(cfg):
    """开跑前把 config.yaml 里会炸的值挑出来。

    宁可在这里退出，也不要跑到一半才发现 profile 拼错、每批 0 段。

    Args:
        cfg (dict): config.load() 的返回值。

    Returns:
        list[str]: 每条一个问题，空列表表示配置没毛病。
    """
    problems = []
    if not cfg.get("api_key"):
        problems.append("没有 api_key。把 secrets.example.yaml 抄成 secrets.yaml "
                        "填进去，或者设环境变量 DEEPSEEK_API_KEY")
    if cfg["profile"] not in cfg["profiles"]:
        problems.append(f"profile 是 {cfg['profile']}，profiles 里没有这一档")
    if cfg["profile"] not in cfg["profile_max_chars"]:
        problems.append(f"profile_max_chars 里没有 {cfg['profile']}，质检会炸")
    if cfg["dialogue_gen_per_call"] < 1:
        problems.append("dialogue_gen_per_call 至少是 1")
    if cfg["concurrency"] < 1:
        problems.append("concurrency 至少是 1")
    if not 1 <= cfg["min_turns"] <= cfg["max_turns"]:
        problems.append(f"轮数区间不成立：min_turns {cfg['min_turns']}，"
                        f"max_turns {cfg['max_turns']}")
    return problems


def main():
    """入口：预估 → 确认 → 并发生成 → 解析 → 质检。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="不问直接跑")
    ap.add_argument("--plan", action="store_true", help="只打印预估和余额，不生成")
    ap.add_argument("--preview", action="store_true", help="只打印拼好的 system 和 user")
    args = ap.parse_args()

    cfg = config.load()
    problems = check_config(cfg)
    if problems:
        sys.exit("\n".join(problems))

    system = llm.build_system(cfg)
    tasks, skipped = build_tasks(cfg)
    if not tasks:
        sys.exit(f"{cfg['input_dir']} 里没有可用的讨论点 JSON")

    if args.preview:
        print_preview(cfg, tasks, system)
        return

    users = [llm.build_user(cfg, t["domain"], t["keyword"], t["opinion"], t["points"])
             for t in tasks]
    n_dialogues = sum(len(t["points"]) for t in tasks)
    before = accounting.balance(cfg)

    ui.banner("DataAutomation · 讨论点 → 多轮对话")
    panel_input(tasks, skipped)
    panel_params(cfg)
    panel_estimate(cfg, system, users, n_dialogues, before)

    if args.plan:
        print()
        return
    if not args.yes and input(f"\n{ui.INDENT}继续？[y/N] ").strip().lower() != "y":
        return

    collected, fails = generate_all(cfg, system, tasks)
    panel_result(cfg, collected, fails, before)

    written = parser_.parse_all(cfg)
    checker.check_all(cfg)
    if written:
        ui.panel("落盘", [ui.ok(f"{ui.OK} {p.relative_to(ROOT)}") for p in written])
    print()


if __name__ == "__main__":
    main()
