"""数 token、汇总用量、查账户余额。

本文件按四组定义函数：

数 token 组

- `_Get_Tokenizer`：建好本地词表对象并缓存，只被下面两个调用。
- `Is_Exact`：告诉调用方现在的 token 数是真数出来的还是按字数估的。
- `Count_Tokens`：一段文本多少 token。

汇总组

- `Merge_Usage`：把若干次请求返回的官方用量对象加成一个。
- `Format_Usage`：汇总结果排成一行，给面板用。
- `Format_Usage_Line`：汇总结果压成定宽的一行，给屏幕底部常驻那块用。
- `Usage_Rows`：汇总结果拆成表格行，给 `ui.Table` 用。

预估组

- `Estimate_Usage`：开跑前拍一份用量，字段名和官方对齐，能直接喂给上面三个。

余额组

- `Query_Balance`：调官方接口查账户余额。
- `Format_Balance`：把跑前跑后两个余额排成一行。

数字一律用 DeepSeek 官方给的：每次请求的用量取响应体里的 `usage` 对象（接口
自己返回的 token 计数），账户余额调 `GET /user/balance`。本地不维护价目表，
也不把 token 换算成钱。跑之前还没有请求可查，那时候输入部分用
`tokenizer/deepseek.json`（DeepSeek 官方词表文件）真数一遍，输出部分只能按
字数拍。

单独查一次余额：

    python scripts/usage.py
"""

import json
import sys
import urllib.error
import urllib.request
from decimal import Decimal

import config


# ---------- 配置区 ----------

# 官方词表文件，仓库里带着，数 token 时不联网。
TOKENIZER_PATH = config.ROOT / "tokenizer" / "deepseek.json"

# tokenizers 这个包装不上时退回按字符估：1 个 token 约等于 1.5 个中文字符。
CHARS_PER_TOKEN = 1.5

# 模型每条消息平均多少字。对话还没生成出来，输出量只能按这个拍。
CHARS_PER_MESSAGE = 25

# 打印用量时按这个顺序取字段，取不到或者为 0 就不印。键名照抄 DeepSeek 响应体，方便对照官方文档。
SHOWN_FIELDS = [
    ("prompt_cache_miss_tokens", "输入未命中"),
    ("prompt_cache_hit_tokens", "输入命中缓存"),
    ("completion_tokens", "输出"),
    ("reasoning_tokens", "其中思考"),
]

# 屏幕底部常驻那一行只印这三项，且宽度固定，跑的时候数字长大也不左右跳。
LINE_FIELDS = [
    ("prompt_cache_miss_tokens", "输入未命中"),
    ("prompt_cache_hit_tokens", "命中缓存"),
    ("completion_tokens", "输出"),
]

# 建好的词表对象存这里，只建一次。None 表示还没试过，False 表示试过但建不起来。
_TOKENIZER_CACHE = None


# ---------- 数 token ----------


def _Get_Tokenizer():
    """
    拿到建好的词表对象，拿不到就返回 None。

    tokenizers 这个第三方包没装、或者 `tokenizer/deepseek.json` 这个词表文件
    不在，都返回 None，让调用方退回按字符估。少一个精确的 token 计数不该让整条
    流水线跑不起来。

    Returns:
        tokenizers 库的 Tokenizer 实例，建不起来时返回 None。
    """

    global _TOKENIZER_CACHE

    # None 表示还没试过，试一次之后要么存实例要么存 False，不重复试
    if _TOKENIZER_CACHE is None:
        try:

            # tokenizers 是 HuggingFace 出的分词库，from_file 直接吃官方那份 json 词表
            from tokenizers import Tokenizer
            _TOKENIZER_CACHE = Tokenizer.from_file(str(TOKENIZER_PATH))

        except Exception:
            _TOKENIZER_CACHE = False

    return _TOKENIZER_CACHE or None


def Is_Exact() -> bool:
    """
    现在的 token 数是真数出来的还是估出来的。

    屏幕上要据此写明数字的来源，估出来的不能让人当成准数看。

    Returns:
        真跑分词返回 True，退回按字数估返回 False。
    """

    return _Get_Tokenizer() is not None


def Count_Tokens(text: str) -> int:
    """
    数一段文本有多少 token。

    有词表就真跑一遍分词，没有就按字符数除以 `CHARS_PER_TOKEN` 估。数出来的是
    纯文本的 token 数，不含 chat 模板那几个固定 token（接口在消息前后自动加的
    角色标记），所以会比接口返回的 `prompt_tokens` 略少几个。

    Args:
        text: 任意文本。
    Returns:
        token 数。
    """

    tokenizer_obj = _Get_Tokenizer()
    if tokenizer_obj is None:
        return int(len(text) / CHARS_PER_TOKEN)

    # add_special_tokens 关掉，只数正文，不让它自己往前后加起止标记
    return len(tokenizer_obj.encode(text, add_special_tokens = False).ids)


# ---------- 汇总官方用量 ----------


def Merge_Usage(usage_list: list) -> dict:
    """
    把若干次请求返回的官方用量对象加成一个。

    不挑字段，DeepSeek 给什么就统计什么。数值型的逐项相加，嵌套的
    `prompt_tokens_details` 和 `completion_tokens_details`（官方把细分项放在
    这两个子字典里）摊平到顶层，`reasoning_tokens` 就是从这里来的。以后接口
    新增字段，这里不用改也能跟着统计到。

    Args:
        usage_list: 每次请求响应体里的 `usage`，原样传进来。
    Returns:
        字段名照抄官方，值是各次之和。
    """

    total_usage = {}
    for one_usage in usage_list:
        for field_name, field_value in (one_usage or {}).items():

            # 嵌套的细分项摊平一层，把里面的整数直接提到顶层同名键上
            if isinstance(field_value, dict):
                for sub_name, sub_value in field_value.items():
                    if isinstance(sub_value, int):
                        total_usage[sub_name] = total_usage.get(sub_name, 0) + sub_value

            elif isinstance(field_value, int):
                total_usage[field_name] = total_usage.get(field_name, 0) + field_value

    return total_usage


def Format_Usage(total_usage: dict) -> str:
    """
    把汇总后的用量排成一行给人看，单位是万。

    Args:
        total_usage: `Merge_Usage` 的返回值，或者形状相同的预估。
    Returns:
        形如「输入未命中 0.3 万  输入命中缓存 12.8 万  输出 7.3 万」，全为 0 时返回「无」。
    """

    parts = [
        f"{label} {total_usage[key_name] / 1e4:.1f} 万"
        for key_name, label in SHOWN_FIELDS if total_usage.get(key_name)
    ]
    return "  ".join(parts) if parts else "无"


def Format_Usage_Line(total_usage: dict) -> str:
    """
    把用量压成定宽的一行，给屏幕底部常驻那块用。

    数值右对齐到 7 格，跑的过程中数字一直在长大，固定宽度才不会左右跳。

    Args:
        total_usage: `Merge_Usage` 的返回值。
    Returns:
        形如「输入未命中  20,700  命中缓存  19,200  输出   1,440」。
    """

    return "  ".join(
        f"{label} {total_usage.get(key_name, 0):>7,}"
        for key_name, label in LINE_FIELDS
    )


def Usage_Rows(total_usage: dict) -> list:
    """
    把汇总后的用量拆成表格行。

    Args:
        total_usage: `Merge_Usage` 的返回值，或者形状相同的预估。
    Returns:
        形如 `[["输出", "3,640"], ...]`，值为 0 的项不出现。喂给 `ui.Table`。
    """

    return [
        [label, f"{total_usage[key_name]:,}"]
        for key_name, label in SHOWN_FIELDS if total_usage.get(key_name)
    ]


# ---------- 开跑前的预估 ----------


def Estimate_Usage(cfg: dict, system_text: str, user_texts: list, dialogue_count: int) -> dict:
    """
    开跑前拍一份用量，字段名和官方对齐，能直接喂给上面几个格式化函数。

    输入部分是现成的文本，用词表真数。输出部分还不存在，只能按每条消息多少字拍。

    system 消息每次调用一字不差，走 DeepSeek 的前缀缓存，只有最先发出去的那几路
    会算未命中，后面的都按命中计价。并发几路就按几路未命中算，估得保守一点。
    输出按 `max_turns` 算，也往贵了估。

    Args:
        cfg: `config.Load_Config` 的返回值，用到 concurrency 和 max_turns。
        system_text: 拼好的 system 消息。
        user_texts: 每次调用的 user 消息。
        dialogue_count: 这一趟一共要产出几段对话。
    Returns:
        字段名照抄官方 usage 的字典。
    """

    system_tokens = Count_Tokens(system_text)

    # 并发几路，最先发出去的就有几路撞不上缓存；调用总数比并发少时按调用总数算
    miss_call_count = min(cfg["concurrency"], len(user_texts))

    return {
        "prompt_cache_miss_tokens": (
            system_tokens * miss_call_count + sum(Count_Tokens(u) for u in user_texts)
        ),
        "prompt_cache_hit_tokens": system_tokens * max(0, len(user_texts) - miss_call_count),
        "completion_tokens": int(
            dialogue_count * cfg["max_turns"] * 2 * CHARS_PER_MESSAGE / CHARS_PER_TOKEN
        ),
    }


# ---------- 查余额 ----------


def Query_Balance(cfg: dict):
    """
    查账户余额，走官方接口 `GET /user/balance`。

    跑前跑后各查一次就能看出这一趟大概扣了多少，不需要在本地维护一份会过期的
    价目表。任何异常都返回 None，查不到余额不该让整趟跑挂掉。

    Args:
        cfg: `config.Load_Config` 的返回值，用到 base_url、api_key、timeout。
    Returns:
        `(余额, 币种)` 这样一个元组，查不到时返回 None。
    """

    request_obj = urllib.request.Request(
        cfg["base_url"].rstrip("/") + "/user/balance",
        headers = {"Authorization": "Bearer " + cfg["api_key"]},
    )

    try:
        with urllib.request.urlopen(request_obj, timeout = cfg["timeout"]) as response_obj:
            body_data = json.loads(response_obj.read().decode("utf-8"))

        # 账号可能同时挂着 USD 和 CNY 两格，其中一格是 0。挑有钱的那格，都是 0 就用第一格
        balance_infos = body_data["balance_infos"]
        picked = next(
            (one for one in balance_infos if Decimal(one["total_balance"])),
            balance_infos[0],
        )
        return Decimal(picked["total_balance"]), picked["currency"]

    except (urllib.error.URLError, KeyError, IndexError, ValueError):
        return None


def Format_Balance(before, after) -> str:
    """
    把跑前跑后两次余额排成一行给人看。

    两个数一样是正常的，DeepSeek 扣费有几分钟延迟，不代表这一趟没花钱。

    Args:
        before: 跑之前 `Query_Balance` 的返回值。
        after: 跑完之后 `Query_Balance` 的返回值。
    Returns:
        形如「97.31 → 96.85 CNY（扣费有延迟，两个数一样是正常的）」，任一次没查到就返回「查不到」。
    """

    if not before or not after:
        return "查不到"

    return f"{before[0]} → {after[0]} {after[1]}（扣费有延迟，两个数一样是正常的）"


if __name__ == "__main__":

    current_balance = Query_Balance(config.Load_Config())
    if not current_balance:
        sys.exit("查不到余额")

    print(f"余额 {current_balance[0]} {current_balance[1]}")
