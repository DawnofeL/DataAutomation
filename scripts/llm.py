"""拼提示词、调模型。这个文件只有这两件事。

本文件按调用顺序定义九个函数：

拼提示词组

- `_Fill_Template`：读 `prompts/` 下的骨架文件，把里面的 `{占位符}` 换成实际内容。
  下面四个 Build 全靠它。
- `Read_System_Slots`：读出要填进 system 骨架的四份文本（人设和三份规则文档）。
- `Build_System`：拼出完整的 system 消息。
- `Split_System_Parts`：把 system 拆成骨架和四份文本，用来统计每块占多少 token。
- `Build_User`：拼出这一批的 user 消息。
- `Build_Retry`：输出不合格时，拼出要追加在 user 末尾的那段毛病清单。

调模型组

- `Chat_Once`：发一次 HTTP 请求，返回官方响应体。
- `Find_Problems`：判这次输出合不合格，标准跟落盘时那道闸门完全一致。
- `Generate_Batch`：一批的完整流程，`Chat_Once` 加 `Find_Problems` 加重发。
  `run.py` 只调这一个。

这个文件不读 `config.yaml`，`cfg` 一律由调用方传进来；不数 token，那在
`usage.py`；不落盘，那在 `parse.py`。

一次调用发出去两条消息，从上往下这么拼：

    ┌─ system ──────────────────────────────────────────────────────────┐
    │  prompts/system.md                     ← 骨架，下面四个填进去      │
    │    {persona}             ← cfg["persona"] 指向的人设文件           │
    │    {words}               ← references/words.md      用词红线       │
    │    {alive_dialogue}      ← references/alive-dialogue.md  活人感    │
    │    {knowledge_honesty}   ← references/knowledge-honesty.md 事实边界│
    │  骨架剩下的部分：任务说明、硬禁令速查、交稿前自查                  │
    └───────────────────────────────────────────────────────────────────┘

    ┌─ user ────────────────────────────────────────────────────────────┐
    │  prompts/user.md                       ← 骨架，下面九个填进去      │
    │    {domain} {keyword} {opinion} {n} {points} {first_id}            │
    │    {min_turns} {max_turns} {answer_length}                         │
    └───────────────────────────────────────────────────────────────────┘

    输出不合格时，在 user 末尾追加 prompts/retry.md，填 {problems} 和轮数区间。

system 每次调用一字不差，走 DeepSeek 的前缀缓存，第二次起这一段按命中计价。
所以一次调用只带一个关键词，不把多个领域揉进一次请求。

看拼出来长什么样，不发请求：

    python scripts/run.py --preview
"""

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

import check
import parse


# ---------- 配置区 ----------

# 项目根目录，也就是 scripts/ 的上一级。
ROOT = Path(__file__).resolve().parent.parent

# 提示词骨架放在这个目录下，三个文件：system.md、user.md、retry.md。
PROMPTS_DIR = ROOT / "prompts"

# references/ 下这三份原样填进 system 骨架的对应占位符。
REFERENCE_FILES = {
    "{words}": "words.md",
    "{alive_dialogue}": "alive-dialogue.md",
    "{knowledge_honesty}": "knowledge-honesty.md",
}

# 占位符对应的中文名，统计每块占多少 token 时打给人看。
SLOT_LABELS = {
    "{persona}": "人设",
    "{words}": "用词",
    "{alive_dialogue}": "活人感",
    "{knowledge_honesty}": "事实边界",
}

# 骨架里合法的占位符长这样，用来核对有没有漏填。
SLOT_PATTERN = r"\{[a-z_]+\}"


# ---------- 拼提示词 ----------


def _Fill_Template(name: str, slots: dict) -> str:
    """
    读 `prompts/` 下的一个骨架文件，把里面的 `{占位符}` 换成实际内容。

    用 `str.replace` 挨个换，不用 `str.format`。references 里的 md 有 JSON 示例，
    正文自带大括号，走 format 会被当成占位符解析然后炸掉。

    换之前先核对一遍：骨架里有几个占位符，`slots` 就得给几个，多一个少一个都
    当场报错。改了 md 忘了改这里的话宁可炸，也别拿一段还带着 `{keyword}` 的
    提示词去调模型。

    Args:
        name: `prompts/` 下的文件名，例如 "user.md"。
        slots: 键要自带大括号，例如 `{"{domain}": "两性关系"}`。
    Returns:
        填完的文本。
    Raises:
        ValueError: 骨架里的占位符和 `slots` 的键对不上。
    """

    template_text = (PROMPTS_DIR / name).read_text(encoding = "utf-8")
    found_slots = set(re.findall(SLOT_PATTERN, template_text))

    if found_slots - set(slots):
        raise ValueError(f"{name} 里有占位符没人填：{sorted(found_slots - set(slots))}")

    if set(slots) - found_slots:
        raise ValueError(f"{name} 里没有这些占位符：{sorted(set(slots) - found_slots)}")

    for slot_key, slot_value in slots.items():
        template_text = template_text.replace(slot_key, slot_value)

    return template_text


def Read_System_Slots(cfg: dict) -> dict:
    """
    读出要填进 system 骨架的四份文本。

    人设那份路径由 `cfg["persona"]` 指定，可以换成别的角色；另外三份是固定的
    规则文档。返回的字典顺序就是它们在骨架里出现的顺序。

    Args:
        cfg: `config.Load_Config` 的返回值，用到 persona。
    Returns:
        形如 `{"{persona}": "文本", ...}` 的字典。
    """

    slots = {"{persona}": (ROOT / cfg["persona"]).read_text(encoding = "utf-8").strip()}

    for slot_key, file_name in REFERENCE_FILES.items():
        slots[slot_key] = (ROOT / "references" / file_name).read_text(encoding = "utf-8").strip()

    return slots


def Build_System(cfg: dict) -> str:
    """
    拼出完整的 system 消息。

    结果跟具体讨论点无关，每次调用一字不差，调用方开跑前拼一次就够，不用每批都拼。

    Args:
        cfg: `config.Load_Config` 的返回值，用到 persona。
    Returns:
        完整的 system 消息。
    """

    return _Fill_Template("system.md", Read_System_Slots(cfg))


def Split_System_Parts(cfg: dict) -> list:
    """
    把 system 拆成几块，用来统计每块占多少 token。

    骨架那一块是把四个占位符全填成空串之后剩下的内容，也就是任务说明、段落之间
    的衔接、硬禁令速查和交稿前自查。

    Args:
        cfg: `config.Load_Config` 的返回值。
    Returns:
        形如 `[("骨架 system.md", "文本"), ("人设", "文本"), ...]`，顺序就是拼装顺序。
    """

    slots = Read_System_Slots(cfg)
    skeleton_text = _Fill_Template("system.md", {one_key: "" for one_key in slots})

    return [("骨架 system.md", skeleton_text)] + [
        (SLOT_LABELS[one_key], one_value) for one_key, one_value in slots.items()
    ]


def Build_User(cfg: dict, domain: str, keyword: str, opinion: str, points: list) -> str:
    """
    拼出这一批的 user 消息。

    Args:
        cfg: `config.Load_Config` 的返回值，用到 min_turns、max_turns、profile、profiles。
        domain: 领域名，取自输入 JSON 的 domain。
        keyword: 这一批讨论点属于哪个关键词。
        opinion: assistant 在这个领域的立场，取自输入 JSON 的 opinion。
        points: 这一批讨论点，每条形如 `{"id": "1-1", "point": "..."}`。
    Returns:
        完整的 user 消息。
    """

    return _Fill_Template("user.md", {
        "{domain}": domain,
        "{keyword}": keyword,
        "{opinion}": opinion,
        "{n}": str(len(points)),
        "{points}": "\n".join(f"{one['id']}  {one['point']}" for one in points),

        # 格式示例里填真实编号。写死成「讨论点id」模型会照抄成「=== 讨论点 3-1」
        "{first_id}": points[0]["id"],
        "{min_turns}": str(cfg["min_turns"]),
        "{max_turns}": str(cfg["max_turns"]),
        "{answer_length}": cfg["profiles"][cfg["profile"]],
    })


def Build_Retry(cfg: dict, problems: list) -> str:
    """
    拼出重发时要追加在 user 末尾的那段毛病清单。

    Args:
        cfg: `config.Load_Config` 的返回值，用到 min_turns 和 max_turns。
        problems: `Find_Problems` 挑出来的毛病，一条一行。
    Returns:
        追加文本，直接接在原 user 消息后面。
    """

    return _Fill_Template("retry.md", {
        "{problems}": "\n".join(problems),
        "{min_turns}": str(cfg["min_turns"]),
        "{max_turns}": str(cfg["max_turns"]),
    })


# ---------- 调模型 ----------


def Chat_Once(cfg: dict, system_text: str, user_text: str) -> dict:
    """
    发一次请求，返回官方响应体。

    直接打 HTTP 不用 openai SDK：发出去的每个字段都在下面的 payload 里明文摆着，
    没有第三方库偷偷加参数。走 OpenAI 兼容格式，`base_url` 换成别家也能用。

    payload 里的 thinking 是 DeepSeek v4 的思考开关。v4-flash 默认开思考，思考
    产生的 reasoning_tokens 按输出价计费，关掉能省几倍钱但轮数写不满，由
    `cfg["thinking"]` 决定。

    Args:
        cfg: `config.Load_Config` 的返回值，用到 base_url、api_key、model、thinking、timeout。
        system_text: `Build_System` 的结果。
        user_text: `Build_User` 的结果，重发时末尾已经接上 `Build_Retry`。
    Returns:
        完整的响应 JSON，一个字段都没动过。正文在 `["choices"][0]["message"]["content"]`，
        官方用量统计在 `["usage"]`。
    Raises:
        RuntimeError: HTTP 状态码不是 200、连不上、响应不是 JSON、或者响应里没有 choices。
    """

    payload = {
        "model": cfg["model"],
        "thinking": {"type": "enabled" if cfg["thinking"] else "disabled"},
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text},
        ],
    }

    request_obj = urllib.request.Request(
        cfg["base_url"].rstrip("/") + "/chat/completions",
        data = json.dumps(payload, ensure_ascii = False).encode("utf-8"),
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + cfg["api_key"],
        },
        method = "POST",
    )

    try:
        with urllib.request.urlopen(request_obj, timeout = cfg["timeout"]) as response_obj:
            body_data = json.loads(response_obj.read().decode("utf-8"))

    except urllib.error.HTTPError as one_error:

        # 把响应体前 200 字带上，接口报的余额不足、鉴权失败这些原因就在里面
        detail_text = one_error.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"HTTP {one_error.code}  {detail_text}") from one_error

    except urllib.error.URLError as one_error:
        raise RuntimeError(f"连不上 {cfg['base_url']}  {one_error.reason}") from one_error

    except json.JSONDecodeError as one_error:
        raise RuntimeError(f"响应不是 JSON  {one_error}") from one_error

    if not body_data.get("choices"):
        raise RuntimeError(f"响应里没有 choices  {json.dumps(body_data, ensure_ascii = False)[:200]}")

    return body_data


def Find_Problems(cfg: dict, text: str, points: list) -> list:
    """
    判这一次输出合不合格。

    标准跟 `parse.py` 落盘时那道闸门完全一致：先用 `parse.Parse_Raw` 拆开、
    `parse.Validate_Structure` 查结构，结构过了再用 `check.Hard_Problems_Of`
    查内容。所以这里报合格的批次，落盘时一定全收，不会出现「跑完说成功、
    落盘时被打回」。

    查的东西：讨论点有没有漏、有没有多出不属于这一批的、有没有同一条写两遍、
    轮数和角色顺序对不对，以及禁词、问句结尾、占位符这些内容硬失败。

    Args:
        cfg: `config.Load_Config` 的返回值。
        text: 模型返回的原文。
        points: 这一批要的讨论点。
    Returns:
        毛病清单，一条一行。空列表表示合格。可以直接喂给 `Build_Retry`。
    """

    want_ids = {one["id"] for one in points}
    problems = []
    seen_ids = set()

    for discussion_id, messages in parse.Parse_Raw(text):

        # 同一个编号写了两遍，多的那一遍要删掉，不然落盘时产出数会比讨论点数多
        if discussion_id in seen_ids:
            problems.append(f"{discussion_id} 写了两遍，只留一段")
            continue

        seen_ids.add(discussion_id)

        if discussion_id not in want_ids:
            problems.append(f"{discussion_id} 不属于这一批，删掉")
            continue

        # 结构坏了就不往下查内容了，一段轮数都不对，内容毛病列出来也没意义
        structural_problems = parse.Validate_Structure(discussion_id, messages, cfg)
        if structural_problems:
            problems += structural_problems
            continue

        one_record = {"messages": [{"role": r, "content": c} for r, c in messages]}
        problems += [
            f"{discussion_id} {one}"
            for one in check.Hard_Problems_Of(one_record, cfg, cfg["profile"])
        ]

    problems += [f"{one_id} 整条没写" for one_id in sorted(want_ids - seen_ids)]
    return problems


def Generate_Batch(cfg: dict, system_text: str, domain: str, keyword: str,
                   opinion: str, points: list) -> tuple:
    """
    要一批对话，不合格就带着毛病清单让模型重写。

    合格标准就是 `Find_Problems` 那套。不合格时把清单追加到 user 末尾重发，
    最多重发 `cfg["max_retry"]` 次。每次重发都从原始 user 重新接，不把上一轮的
    清单叠上去，否则 user 会越滚越长。

    重发用光还不合格也照样返回最后那份原文。里面往往只有一两段有问题，其余能救，
    整批丢掉等于连累旁边那几段，逐段分流交给 `parse.py`。

    Args:
        cfg: `config.Load_Config` 的返回值，用到 max_retry，其余透传。
        system_text: `Build_System` 的结果，外面拼一次传进来。
        domain: 领域名。
        keyword: 这一批讨论点属于哪个关键词。
        opinion: assistant 在这个领域的立场。
        points: 这一批讨论点。
    Returns:
        `(原文, 每次请求的官方 usage 列表, 错在哪)`。成功时第三项是 None，
        失败时第一项是最后一次拿到的原文，可能是空串。
    """

    base_user_text = Build_User(cfg, domain, keyword, opinion, points)
    user_text = base_user_text
    usage_list = []
    last_text = ""
    error_text = None

    for attempt_index in range(cfg["max_retry"] + 1):

        try:
            body_data = Chat_Once(cfg, system_text, user_text)

        except RuntimeError as one_error:

            # 网络或接口层面的错，原样再发一次，不追加毛病清单
            error_text = str(one_error)
            continue

        if body_data.get("usage"):
            usage_list.append(body_data["usage"])

        last_text = body_data["choices"][0]["message"]["content"] or ""

        problems = Find_Problems(cfg, last_text, points)
        if not problems:
            return last_text, usage_list, None

        # 只在错误摘要里留前两条，全列出来屏幕上一行放不下
        error_text = "；".join(problems[:2]) + ("…" if len(problems) > 2 else "")
        user_text = base_user_text + Build_Retry(cfg, problems)

    return last_text, usage_list, error_text
