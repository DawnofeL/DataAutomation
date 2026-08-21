#!/usr/bin/env python3
"""上下文组装 + 推理。所有和大模型打交道的代码都在这一个文件里。

其他脚本一律不碰提示词、不碰 HTTP：
    run.py    只负责分批、并发、落盘、打印
    parse.py  只负责把返回的纯文本拼成 JSON
    check.py  只负责质检落盘后的 JSON

一次调用发出去两条消息，从上往下这么拼：

    ┌─ system ──────────────────────────────────────────────────────────┐
    │  prompts/system.md                     ← 骨架，下面四个是填进去的  │
    │    {persona}             ← config.persona 指向的文件               │
    │    {words}               ← references/words.md                     │
    │    {alive_dialogue}      ← references/alive-dialogue.md            │
    │    {knowledge_honesty}   ← references/knowledge-honesty.md         │
    │  骨架剩下的部分：硬禁令速查、交稿前自查                            │
    └───────────────────────────────────────────────────────────────────┘

    ┌─ user ────────────────────────────────────────────────────────────┐
    │  prompts/user.md                       ← 骨架，下面七个是填进去的  │
    │    {domain}          ← input JSON 的 domain                        │
    │    {keyword}         ← 这一批讨论点属于哪个关键词                  │
    │    {opinion}         ← input JSON 的 opinion                       │
    │    {n}               ← 这一批有几条讨论点                          │
    │    {points}          ← 「id  两个空格  point」逐行列出             │
    │    {turns}           ← config.turns                                │
    │    {answer_length}   ← config.profiles[config.profile]             │
    └───────────────────────────────────────────────────────────────────┘

    重发时（模型漏了讨论点）在 user 后面追加：
    ┌───────────────────────────────────────────────────────────────────┐
    │  prompts/retry.md                                                  │
    │    {missing}         ← 缺了哪几个 id                               │
    └───────────────────────────────────────────────────────────────────┘

system 每次调用一字不差，走 DeepSeek 的 prompt cache，第二次起这段按 cache hit
计价（便宜 30 倍）。所以一次调用只带一个 keyword，不把多个领域揉进一次请求。

看拼出来长什么样，不发请求：

    python scripts/run.py --preview
"""

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

import yaml

import parse

ROOT = Path(__file__).resolve().parent.parent

# 中文粗估：1 个 token 约 1.5 个字符。只用于跑之前估算，不用于对账。
CHARS_PER_TOKEN = 1.5

# 模型每条消息平均多少字，估算输出量用。
CHARS_PER_MESSAGE = 25

# 用量字段，累加时只认这三个。
USAGE_KEYS = ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "completion_tokens")


# ====================================================================
#  配置
# ====================================================================


def load_config():
    """读根目录的 config.yaml。

    Returns:
        dict: config.yaml 的全部内容。
    """
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


# ====================================================================
#  上下文组装
# ====================================================================


def _fill(template, slots):
    """把模板里的 {占位符} 换成对应文本。

    用 str.replace 挨个换，不用 str.format。references/ 下的 md 里有 JSON 示例，
    正文自带大括号，走 format 会被当成占位符解析然后炸掉。

    换之前先核对一遍：模板里有几个占位符，slots 就得给几个，多一个少一个都报错，
    免得改了 md 忘了改这里，最后拿一段带着 {keyword} 的提示词去调模型。

    Args:
        template (str): 含 {xxx} 占位符的模板文本。
        slots (dict[str, str]): 键要自带大括号，例如 {"{domain}": "两性关系"}。

    Returns:
        str: 填完的文本。

    Raises:
        ValueError: 模板里的占位符和 slots 的键对不上。
    """
    found = set(re.findall(r"\{[a-z_]+\}", template))
    missing = found - set(slots)
    extra = set(slots) - found
    if missing:
        raise ValueError(f"模板里有占位符没人填：{sorted(missing)}")
    if extra:
        raise ValueError(f"给了模板里不存在的占位符：{sorted(extra)}")
    for key, value in slots.items():
        template = template.replace(key, value)
    return template


def build_system(cfg):
    """拼 system 消息：骨架 prompts/system.md + 人设 + 三份 references。

    结果跟具体讨论点无关，每次调用都一样，run.py 只在开跑前调一次。

    Args:
        cfg (dict): load_config() 的返回值。

    Returns:
        str: 完整的 system 消息。
    """
    skeleton = (ROOT / "prompts" / "system.md").read_text(encoding="utf-8")
    slots = {
        "{persona}": ROOT / cfg["persona"],
        "{words}": ROOT / "references" / "words.md",
        "{alive_dialogue}": ROOT / "references" / "alive-dialogue.md",
        "{knowledge_honesty}": ROOT / "references" / "knowledge-honesty.md",
    }
    return _fill(skeleton, {k: v.read_text(encoding="utf-8").strip() for k, v in slots.items()})


def build_user(cfg, domain, keyword, opinion, points):
    """拼 user 消息：骨架 prompts/user.md + 这一批讨论点 + 长度形状。

    Args:
        cfg (dict): load_config() 的返回值。
        domain (str): 领域，input JSON 的 domain。
        keyword (str): 这一批讨论点属于哪个关键词。
        opinion (str): assistant 在这个领域的立场，input JSON 的 opinion。
        points (list[dict]): 这一批讨论点，每条形如 {"id": "1-1", "point": "..."}。

    Returns:
        str: 完整的 user 消息。
    """
    skeleton = (ROOT / "prompts" / "user.md").read_text(encoding="utf-8")
    return _fill(skeleton, {
        "{domain}": domain,
        "{keyword}": keyword,
        "{opinion}": opinion,
        "{n}": str(len(points)),
        "{points}": "\n".join(f"{p['id']}  {p['point']}" for p in points),
        "{first_id}": points[0]["id"],
        "{turns}": str(cfg["turns"]),
        "{messages}": str(cfg["turns"] * 2),
        "{answer_length}": cfg["profiles"][cfg["profile"]],
    })


def build_retry(cfg, problems):
    """拼重发时追加在 user 后面的那段：prompts/retry.md。

    Args:
        cfg (dict): load_config() 的返回值。
        problems (list[str]): find_problems() 挑出来的毛病，一条一行。

    Returns:
        str: 追加文本，直接接在原 user 消息末尾。
    """
    skeleton = (ROOT / "prompts" / "retry.md").read_text(encoding="utf-8")
    return _fill(skeleton, {
        "{problems}": "\n".join(problems),
        "{turns}": str(cfg["turns"]),
    })


# ====================================================================
#  推理
# ====================================================================


def chat(cfg, system, user):
    """发一次请求，返回解析好的响应体。

    直接打 HTTP，不用 openai SDK：发出去的每个字段都在下面的 payload 里明文摆着，
    没有第三方库偷偷加参数。走 OpenAI 兼容格式，base_url 换成别家也能用。

    thinking 那一项是 DeepSeek v4 的开关。v4-flash 默认开思考，思考产生的
    reasoning_tokens 按输出价计费，关掉能省几倍钱。config.thinking 控制。

    Args:
        cfg (dict): load_config() 的返回值。用到 base_url / api_key / model /
            thinking / timeout。
        system (str): build_system() 的结果。
        user (str): build_user() 的结果，重发时末尾已经接上 build_retry()。

    Returns:
        dict: DeepSeek 的完整响应 JSON。
            正文在 ["choices"][0]["message"]["content"]，用量在 ["usage"]。

    Raises:
        RuntimeError: HTTP 状态码不是 200、连不上、或者响应里没有 choices。
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

    用的就是 parse.py 那套解析和校验，标准跟落盘时一模一样。这样 run.py 报 ok
    的批次，parse.py 一定收得下，不会出现「跑完说成功、解析时全灭」。

    查三件事：讨论点有没有漏、有没有多、每段轮数和角色顺序对不对。
    内容写得好不好一概不管，那是 check.py 的事。

    Args:
        cfg (dict): load_config() 的返回值。
        text (str): 模型返回的原文。
        points (list[dict]): 这一批要的讨论点。

    Returns:
        list[str]: 毛病，一条一行，空列表表示合格。可以直接喂给 build_retry()。
    """
    want = {p["id"] for p in points}
    problems, seen = [], set()
    for did, messages in parse.parse_raw(text):
        seen.add(did)
        if did not in want:
            problems.append(f"{did} 不属于这一批，删掉")
            continue
        problems += parse.validate(did, messages, cfg["turns"])
    problems += [f"{did} 整条没写" for did in sorted(want - seen)]
    return problems


def blank_usage():
    """一份清零的用量字典。

    Returns:
        dict[str, int]: USAGE_KEYS 每一项都是 0。
    """
    return {k: 0 for k in USAGE_KEYS}


def add_usage(total, body):
    """把一次响应的用量累加进 total，原地改。

    Args:
        total (dict[str, int]): blank_usage() 的返回值。
        body (dict): chat() 返回的响应体。
    """
    usage = body.get("usage") or {}
    for k in USAGE_KEYS:
        total[k] += usage.get(k, 0)


def generate(cfg, system, domain, keyword, opinion, points):
    """要一批对话，模型漏条目就带着缺的 id 重发。

    合格标准就是 find_problems() 那套：讨论点不漏不多、每段轮数和角色顺序对。
    不合格就把毛病清单追加到 user 末尾重发，最多重发 config.max_retry 次。
    每次重发都从原始 user 重新接，不把上一轮的毛病清单叠上去。

    Args:
        cfg (dict): load_config() 的返回值。
        system (str): build_system() 的结果，外面建一次传进来。
        domain (str): 领域。
        keyword (str): 这一批讨论点属于哪个关键词。
        opinion (str): assistant 在这个领域的立场。
        points (list[dict]): 这一批讨论点。

    Returns:
        tuple[str, dict, str | None]: (原文, 用量, 错在哪)。
            成功时第三项是 None。失败时第一项是最后一次拿到的原文，可能是空串。
    """
    base = build_user(cfg, domain, keyword, opinion, points)
    user = base
    usage = blank_usage()
    text, error = "", None

    for attempt in range(cfg["max_retry"] + 1):
        try:
            body = chat(cfg, system, user)
        except RuntimeError as e:
            error = str(e)
            continue

        add_usage(usage, body)
        text = body["choices"][0]["message"]["content"] or ""
        problems = find_problems(cfg, text, points)
        if not problems:
            return text, usage, None

        error = "；".join(problems[:2]) + ("…" if len(problems) > 2 else "")
        user = base + build_retry(cfg, problems)

    return text, usage, error


# ====================================================================
#  估算与对账
# ====================================================================


def estimate_tokens(text):
    """按字符数粗估 token 数。

    Args:
        text (str): 任意文本。

    Returns:
        float: 估算出的 token 数。
    """
    return len(text) / CHARS_PER_TOKEN


def estimate_usage(cfg, system, users, n_dialogues):
    """跑之前估一份用量。

    system 每次调用一字不差，只有最先发出去的那几路会 cache miss，后面都命中。
    并发几路就按几路算 miss，估得保守一点。

    Args:
        cfg (dict): load_config() 的返回值。
        system (str): build_system() 的结果。
        users (list[str]): 每次调用的 user 消息。
        n_dialogues (int): 这一趟一共要产出几段对话。

    Returns:
        dict[str, int]: 和 blank_usage() 同构，可以直接喂给 cost()。
    """
    n_calls = len(users)
    sys_tokens = estimate_tokens(system)
    miss_calls = min(cfg["concurrency"], n_calls)
    return {
        "prompt_cache_miss_tokens": int(
            sys_tokens * miss_calls + sum(estimate_tokens(u) for u in users)
        ),
        "prompt_cache_hit_tokens": int(sys_tokens * max(0, n_calls - miss_calls)),
        "completion_tokens": int(
            n_dialogues * cfg["turns"] * 2 * CHARS_PER_MESSAGE / CHARS_PER_TOKEN
        ),
    }


def cost(cfg, usage):
    """按 config.price 把用量折成钱。

    Args:
        cfg (dict): load_config() 的返回值。
        usage (dict[str, int]): blank_usage() 同构的用量。

    Returns:
        float: 美元。config 里没写 price 就返回 0。
    """
    price = cfg.get("price") or {}
    return (
        usage["prompt_cache_hit_tokens"] / 1e6 * price.get("input_cache_hit", 0)
        + usage["prompt_cache_miss_tokens"] / 1e6 * price.get("input_cache_miss", 0)
        + usage["completion_tokens"] / 1e6 * price.get("output", 0)
    )


def format_usage(cfg, usage):
    """把用量和钱排成一行给人看。

    Args:
        cfg (dict): load_config() 的返回值。
        usage (dict[str, int]): blank_usage() 同构的用量。

    Returns:
        str: 形如「输入 21.9 万（命中 14.6 万）  输出 1.6 万  $0.05」。
    """
    hit = usage["prompt_cache_hit_tokens"]
    miss = usage["prompt_cache_miss_tokens"]
    out = usage["completion_tokens"]
    return (f"输入 {(hit + miss) / 1e4:.1f} 万（命中 {hit / 1e4:.1f} 万）  "
            f"输出 {out / 1e4:.1f} 万  ${cost(cfg, usage):.2f}")
