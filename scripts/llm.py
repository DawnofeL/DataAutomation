#!/usr/bin/env python3
"""提示词拼装 + 调模型。这个文件只有这两件事。

    拼装    build_system / build_user / build_retry，骨架在 prompts/ 下
    调用    chat 发一次请求，generate 管重发

不在这里的东西：读配置在各个入口脚本自己手上，token 和花费在 usage.py，
纯文本转 JSON 在 parse.py，质检在 check.py。这个文件不读 config.yaml，
cfg 一律由调用方传进来。

一次调用发出去两条消息，从上往下这么拼：

    ┌─ system ──────────────────────────────────────────────────────────┐
    │  prompts/system.md                     ← 骨架，下面四个填进去      │
    │    {persona}             ← cfg["persona"] 指向的文件               │
    │    {words}               ← references/words.md                     │
    │    {alive_dialogue}      ← references/alive-dialogue.md            │
    │    {knowledge_honesty}   ← references/knowledge-honesty.md         │
    │  骨架剩下的部分：硬禁令速查、交稿前自查                            │
    └───────────────────────────────────────────────────────────────────┘

    ┌─ user ────────────────────────────────────────────────────────────┐
    │  prompts/user.md                       ← 骨架，下面九个填进去      │
    │    {domain}          ← input JSON 的 domain                        │
    │    {keyword}         ← 这一批讨论点属于哪个关键词                  │
    │    {opinion}         ← input JSON 的 opinion                       │
    │    {n}               ← 这一批有几条讨论点                          │
    │    {points}          ← 「id  两个空格  point」逐行列出             │
    │    {first_id}        ← 这一批第一条讨论点的 id，当格式示例         │
    │    {min_turns}       ← cfg["min_turns"]                            │
    │    {max_turns}       ← cfg["max_turns"]                            │
    │    {answer_length}   ← cfg["profiles"][cfg["profile"]]             │
    └───────────────────────────────────────────────────────────────────┘

    输出不合格时，在 user 末尾追加：
    ┌───────────────────────────────────────────────────────────────────┐
    │  prompts/retry.md                                                  │
    │    {problems}        ← find_problems() 挑出来的毛病清单            │
    │    {min_turns}       ← cfg["min_turns"]                            │
    │    {max_turns}       ← cfg["max_turns"]                            │
    └───────────────────────────────────────────────────────────────────┘

system 每次调用一字不差，走 DeepSeek 的 prompt cache，第二次起这段按 cache hit
计价。所以一次调用只带一个 keyword，不把多个领域揉进一次请求。

看拼出来长什么样，不发请求：

    python scripts/run.py --preview
"""

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

import parse

ROOT = Path(__file__).resolve().parent.parent

# 提示词骨架所在目录。
PROMPTS = ROOT / "prompts"

# references/ 下这三份原样填进 system.md。
REFERENCES = {
    "{words}": "words.md",
    "{alive_dialogue}": "alive-dialogue.md",
    "{knowledge_honesty}": "knowledge-honesty.md",
}

# 占位符对应的中文名，统计 token 时打给人看。
LABELS = {
    "{persona}": "人设",
    "{words}": "用词",
    "{alive_dialogue}": "活人感",
    "{knowledge_honesty}": "事实边界",
}


# ====================================================================
#  提示词拼装
# ====================================================================


def _fill(name, slots):
    """读 prompts/<name>，把里面的 {占位符} 换成对应文本。

    用 str.replace 挨个换，不用 str.format。references/ 下的 md 里有 JSON 示例，
    正文自带大括号，走 format 会被当成占位符解析然后炸掉。

    换之前先核对一遍：骨架里有几个占位符，slots 就得给几个，多一个少一个都当场
    报错。改了 md 忘了改这里的话，宁可炸，也不要拿一段带着 {keyword} 的提示词去
    调模型。

    Args:
        name (str): prompts/ 下的文件名，例如 "user.md"。
        slots (dict[str, str]): 键要自带大括号，例如 {"{domain}": "两性关系"}。

    Returns:
        str: 填完的文本。

    Raises:
        ValueError: 骨架里的占位符和 slots 的键对不上。
    """
    text = (PROMPTS / name).read_text(encoding="utf-8")
    found = set(re.findall(r"\{[a-z_]+\}", text))
    if found - set(slots):
        raise ValueError(f"{name} 里有占位符没人填：{sorted(found - set(slots))}")
    if set(slots) - found:
        raise ValueError(f"{name} 里没有这些占位符：{sorted(set(slots) - found)}")
    for key, value in slots.items():
        text = text.replace(key, value)
    return text


def system_slots(cfg):
    """读出要填进 system.md 的四份文本，按拼装顺序。

    Args:
        cfg (dict): config.yaml 的内容。用到 persona。

    Returns:
        dict[str, str]: {占位符: 文本}，顺序就是它们在 system.md 里出现的顺序。
    """
    slots = {"{persona}": (ROOT / cfg["persona"]).read_text(encoding="utf-8").strip()}
    for key, filename in REFERENCES.items():
        slots[key] = (ROOT / "references" / filename).read_text(encoding="utf-8").strip()
    return slots


def build_system(cfg):
    """拼 system 消息：骨架 prompts/system.md + 人设 + 三份 references。

    结果跟具体讨论点无关，每次调用一字不差，调用方开跑前拼一次就够。

    Args:
        cfg (dict): config.yaml 的内容。用到 persona。

    Returns:
        str: 完整的 system 消息。
    """
    return _fill("system.md", system_slots(cfg))


def system_parts(cfg):
    """system 消息拆成几块，用来统计每块占多少 token。

    骨架那一块是 system.md 把四个占位符全填空串之后剩下的内容，也就是
    任务说明、段落之间的衔接、硬禁令速查和交稿前自查。

    Args:
        cfg (dict): config.yaml 的内容。

    Returns:
        list[tuple[str, str]]: [(块名, 文本), ...]，顺序就是拼装顺序。
    """
    slots = system_slots(cfg)
    skeleton = _fill("system.md", {k: "" for k in slots})
    return [("骨架 system.md", skeleton)] + [(LABELS[k], v) for k, v in slots.items()]


def build_user(cfg, domain, keyword, opinion, points):
    """拼 user 消息：骨架 prompts/user.md + 这一批讨论点 + 长度形状。

    Args:
        cfg (dict): config.yaml 的内容。用到 min_turns、max_turns、profile、profiles。
        domain (str): 领域，input JSON 的 domain。
        keyword (str): 这一批讨论点属于哪个关键词。
        opinion (str): assistant 在这个领域的立场，input JSON 的 opinion。
        points (list[dict]): 这一批讨论点，每条形如 {"id": "1-1", "point": "..."}。

    Returns:
        str: 完整的 user 消息。
    """
    return _fill("user.md", {
        "{domain}": domain,
        "{keyword}": keyword,
        "{opinion}": opinion,
        "{n}": str(len(points)),
        "{points}": "\n".join(f"{p['id']}  {p['point']}" for p in points),
        "{first_id}": points[0]["id"],
        "{min_turns}": str(cfg["min_turns"]),
        "{max_turns}": str(cfg["max_turns"]),
        "{answer_length}": cfg["profiles"][cfg["profile"]],
    })


def build_retry(cfg, problems):
    """拼重发时追加在 user 后面的那段：骨架 prompts/retry.md。

    Args:
        cfg (dict): config.yaml 的内容。用到 min_turns、max_turns。
        problems (list[str]): find_problems() 挑出来的毛病，一条一行。

    Returns:
        str: 追加文本，直接接在原 user 消息末尾。
    """
    return _fill("retry.md", {
        "{problems}": "\n".join(problems),
        "{min_turns}": str(cfg["min_turns"]),
        "{max_turns}": str(cfg["max_turns"]),
    })


# ====================================================================
#  调模型
# ====================================================================


def chat(cfg, system, user):
    """发一次请求，返回解析好的响应体。

    直接打 HTTP，不用 openai SDK：发出去的每个字段都在下面的 payload 里明文摆着，
    没有第三方库偷偷加参数。走 OpenAI 兼容格式，base_url 换成别家也能用。

    thinking 是 DeepSeek v4 的开关。v4-flash 默认开思考，思考产生的
    reasoning_tokens 按输出价计费，关掉能省几倍钱，但轮数写不满。

    Args:
        cfg (dict): config.yaml 的内容。用到 base_url、api_key、model、
            thinking、timeout。
        system (str): build_system() 的结果。
        user (str): build_user() 的结果，重发时末尾已经接上 build_retry()。

    Returns:
        dict: DeepSeek 的完整响应 JSON，一个字段都没动过。
            正文在 ["choices"][0]["message"]["content"]，
            官方用量统计在 ["usage"]。

    Raises:
        RuntimeError: HTTP 状态码不是 200、连不上、响应不是 JSON、或者没有 choices。
    """
    payload = {
        "model": cfg["model"],
        "thinking": {"type": "enabled" if cfg["thinking"] else "disabled"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    request = urllib.request.Request(
        cfg["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + cfg["api_key"],
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=cfg["timeout"]) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}  {e.read().decode('utf-8', 'replace')[:200]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"连不上 {cfg['base_url']}  {e.reason}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"响应不是 JSON  {e}") from e

    if not body.get("choices"):
        raise RuntimeError(f"响应里没有 choices  {json.dumps(body, ensure_ascii=False)[:200]}")
    return body


def find_problems(cfg, text, points):
    """挑出模型返回的原文里 parse.py 会拒收的地方。

    用的就是 parse.py 那套解析和校验，标准跟落盘时一模一样。这样报 ok 的批次
    parse.py 一定收得下，不会出现「跑完说成功、解析时全灭」。

    查四件事：讨论点有没有漏、有没有多、有没有重复、每段轮数落没落在
    [min_turns, max_turns] 区间里且角色严格交替。
    内容写得好不好一概不管，那是 check.py 的事。

    Args:
        cfg (dict): config.yaml 的内容。用到 min_turns、max_turns。
        text (str): 模型返回的原文。
        points (list[dict]): 这一批要的讨论点。

    Returns:
        list[str]: 毛病，一条一行，空列表表示合格。可以直接喂给 build_retry()。
    """
    want = {p["id"] for p in points}
    problems, seen = [], set()
    for did, messages in parse.parse_raw(text):
        if did in seen:
            problems.append(f"{did} 写了两遍，只留一段")
            continue
        seen.add(did)
        if did not in want:
            problems.append(f"{did} 不属于这一批，删掉")
            continue
        problems += parse.validate(did, messages, cfg)
    problems += [f"{did} 整条没写" for did in sorted(want - seen)]
    return problems


def generate(cfg, system, domain, keyword, opinion, points):
    """要一批对话，不合格就带着毛病清单重发。

    合格标准就是 find_problems() 那套。不合格时把清单追加到 user 末尾重发，
    最多重发 cfg["max_retry"] 次。每次重发都从原始 user 重新接，不把上一轮的
    清单叠上去。

    Args:
        cfg (dict): config.yaml 的内容。用到 max_retry，其余透传给 chat()
            和 build_user()。
        system (str): build_system() 的结果，外面拼一次传进来。
        domain (str): 领域。
        keyword (str): 这一批讨论点属于哪个关键词。
        opinion (str): assistant 在这个领域的立场。
        points (list[dict]): 这一批讨论点。

    Returns:
        tuple[str, list[dict], str | None]: (原文, 每次请求的官方 usage, 错在哪)。
            usage 原样收集，包括没跑成的那几次，交给 usage.py 汇总。
            成功时第三项是 None，失败时第一项是最后一次拿到的原文，可能是空串。
    """
    base = build_user(cfg, domain, keyword, opinion, points)
    user = base
    usages = []
    text, error = "", None

    for attempt in range(cfg["max_retry"] + 1):
        try:
            body = chat(cfg, system, user)
        except RuntimeError as e:
            error = str(e)
            continue

        if body.get("usage"):
            usages.append(body["usage"])
        text = body["choices"][0]["message"]["content"] or ""

        problems = find_problems(cfg, text, points)
        if not problems:
            return text, usages, None

        error = "；".join(problems[:2]) + ("…" if len(problems) > 2 else "")
        user = base + build_retry(cfg, problems)

    return text, usages, error
