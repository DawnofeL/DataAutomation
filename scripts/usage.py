#!/usr/bin/env python3
"""token 用量和花费。

数字一律用 DeepSeek 官方给的，不自己算价：

    每次请求的用量   响应体里的 usage 对象，字段原样收下，一个都不挑
    账户余额         GET /user/balance

跑之前还没有请求可查，输入部分用 tokenizer/deepseek.json 真数一遍，
输出部分只能按字数拍。

不在这里换算成钱：DeepSeek 扣费有延迟，一趟跑完余额通常纹丝不动，过一阵才扣。
所以余额只跑前跑后各打一次给你看，不拿差值当这一趟的花费。要精确对账去
platform.deepseek.com 的账单页。

    python scripts/usage.py       查一次余额
"""

import json
import sys
import urllib.error
import urllib.request
from decimal import Decimal

import config

# 官方 tokenizer，仓库里带着，不联网。
TOKENIZER = config.ROOT / "tokenizer" / "deepseek.json"

# tokenizers 装不上时退回按字符估：1 个 token 约 1.5 个中文字符。
CHARS_PER_TOKEN = 1.5

# 模型每条消息平均多少字。对话还没生成，输出量只能这么拍。
CHARS_PER_MESSAGE = 25

# Tokenizer 实例只建一次，建好了放这儿。
_TOKENIZER = None

# 打印时按这个顺序取 usage 里的字段，取不到就不印。
# 名字全部照抄 DeepSeek 响应体，不重命名。
SHOWN = [
    ("prompt_cache_miss_tokens", "输入未命中"),
    ("prompt_cache_hit_tokens", "输入命中缓存"),
    ("completion_tokens", "输出"),
    ("reasoning_tokens", "其中思考"),
]


# ====================================================================
#  数 token
# ====================================================================


def _tokenizer():
    """建好的 Tokenizer，或者 None。

    tokenizers 没装、或者 tokenizer/deepseek.json 不在，都返回 None，
    调用方退回按字符估。缺个 token 计数不该让整条流水线跑不起来。

    Returns:
        object | None: tokenizers.Tokenizer 实例。
    """
    global _TOKENIZER
    if _TOKENIZER is None:
        try:
            from tokenizers import Tokenizer

            _TOKENIZER = Tokenizer.from_file(str(TOKENIZER))
        except Exception:
            _TOKENIZER = False
    return _TOKENIZER or None


def exact():
    """token 数是真数出来的还是估出来的。

    Returns:
        bool: 真数返回 True。
    """
    return _tokenizer() is not None


def count(text):
    """一段文本多少 token。

    有 tokenizer 就真跑一遍分词，没有就按字符数除以 CHARS_PER_TOKEN 估。
    数出来的是纯文本的 token 数，不含 chat 模板那几个固定 token，所以会比
    接口返回的 prompt_tokens 略少几个。

    Args:
        text (str): 任意文本。

    Returns:
        int: token 数。
    """
    tokenizer = _tokenizer()
    if tokenizer is None:
        return int(len(text) / CHARS_PER_TOKEN)
    return len(tokenizer.encode(text, add_special_tokens=False).ids)


# ====================================================================
#  官方用量：汇总响应体里的 usage
# ====================================================================


def merge(usages):
    """把若干个官方 usage 对象加成一个。

    不挑字段：DeepSeek 给什么就加什么，数值型的逐项相加，嵌套的
    prompt_tokens_details / completion_tokens_details 摊平到顶层
    （reasoning_tokens、cached_tokens 就是从这里来的）。以后接口加了新字段，
    这里不用改也能跟着统计到。

    Args:
        usages (list[dict]): 每次请求响应体里的 usage，原样传进来。

    Returns:
        dict[str, int]: 字段名照抄官方，值是各次之和。
    """
    total = {}
    for usage in usages:
        for key, value in (usage or {}).items():
            if isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    if isinstance(sub_value, int):
                        total[sub_key] = total.get(sub_key, 0) + sub_value
            elif isinstance(value, int):
                total[key] = total.get(key, 0) + value
    return total


def fmt(total):
    """把汇总后的用量排成一行给人看。

    Args:
        total (dict[str, int]): merge() 的返回值，或者形状相同的预估。

    Returns:
        str: 形如「输入未命中 0.3 万  输入命中缓存 12.8 万  输出 7.3 万」。
    """
    parts = [f"{label} {total[key] / 1e4:.1f} 万" for key, label in SHOWN if total.get(key)]
    return "  ".join(parts) if parts else "无"


def line(total):
    """把用量压成一行，给屏幕底部常驻那块用。

    数值对齐到固定宽度，跑的时候数字在长大，位置不会左右跳。

    Args:
        total (dict[str, int]): merge() 的返回值。

    Returns:
        str: 形如「输入 20,700  命中 19,200  输出 1,440」。
    """
    return "  ".join(
        f"{label} {total.get(key, 0):>7,}"
        for key, label in [("prompt_cache_miss_tokens", "输入未命中"),
                           ("prompt_cache_hit_tokens", "命中缓存"),
                           ("completion_tokens", "输出")]
    )


def rows(total):
    """汇总后的用量拆成表格行，给 ui.table() 用。

    Args:
        total (dict[str, int]): merge() 的返回值，或者形状相同的预估。

    Returns:
        list[list[str]]: [[名目, 数值], ...]，值为 0 的项不出现。
    """
    return [[label, f"{total[key]:,}"] for key, label in SHOWN if total.get(key)]


# ====================================================================
#  开跑前的预估
# ====================================================================


def estimate(cfg, system, users, n_dialogues):
    """开跑前拍一个用量，字段名和官方 usage 对齐，能直接喂给 fmt()。

    输入是现成的文本，用 tokenizer 真数。输出还不存在，只能按每条消息多少字拍。

    system 每次调用一字不差，只有最先发出去的那几路会 cache miss，后面都命中。
    并发几路就按几路 miss 算。输出按 max_turns 算，都往贵了估。

    Args:
        cfg (dict): config.yaml 的内容。用到 concurrency、max_turns。
        system (str): llm.build_system() 的结果。
        users (list[str]): 每次调用的 user 消息。
        n_dialogues (int): 这一趟一共要产出几段对话。

    Returns:
        dict[str, int]: 字段名照抄官方 usage。
    """
    system_tokens = count(system)
    miss_calls = min(cfg["concurrency"], len(users))
    return {
        "prompt_cache_miss_tokens": (
            system_tokens * miss_calls + sum(count(u) for u in users)
        ),
        "prompt_cache_hit_tokens": system_tokens * max(0, len(users) - miss_calls),
        "completion_tokens": int(
            n_dialogues * cfg["max_turns"] * 2 * CHARS_PER_MESSAGE / CHARS_PER_TOKEN
        ),
    }


# ====================================================================
#  真实花费：查余额
# ====================================================================


def balance(cfg):
    """查账户余额。官方接口 GET /user/balance。

    跑前跑后各查一次，相减就是这一趟真花的钱，不需要在本地维护价目表。

    Args:
        cfg (dict): config.yaml 的内容。用到 base_url、api_key、timeout。

    Returns:
        tuple[Decimal, str] | None: (余额, 币种)。查不到返回 None，
            余额只用来打印，不该因为查不到就把整趟跑挂掉。
    """
    request = urllib.request.Request(
        cfg["base_url"].rstrip("/") + "/user/balance",
        headers={"Authorization": "Bearer " + cfg["api_key"]},
    )
    try:
        with urllib.request.urlopen(request, timeout=cfg["timeout"]) as response:
            body = json.loads(response.read().decode("utf-8"))
        info = body["balance_infos"][0]
        return Decimal(info["total_balance"]), info["currency"]
    except (urllib.error.URLError, KeyError, IndexError, ValueError):
        return None


def fmt_balance(before, after):
    """把跑前跑后两次余额排成一行给人看。

    两个数一样是正常的，DeepSeek 扣费有延迟，不代表这一趟没花钱。

    Args:
        before (tuple | None): 跑之前 balance() 的返回值。
        after (tuple | None): 跑完之后 balance() 的返回值。

    Returns:
        str: 形如「97.31 → 96.85 CNY（扣费有延迟，两个数一样是正常的）」。
            查不到就说查不到。
    """
    if not before or not after:
        return "查不到"
    return f"{before[0]} → {after[0]} {after[1]}（扣费有延迟，两个数一样是正常的）"


if __name__ == "__main__":
    current = balance(config.load())
    if not current:
        sys.exit("查不到余额")
    print(f"余额 {current[0]} {current[1]}")
