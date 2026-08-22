"""核对 `check.py` 的词表和 `references/words.md` 对不对得上。

本文件定义两个函数：

- `Extract_Section`：从 words.md 全文里抠出某一节的正文，只被 `Main` 调。
- `Main`：把两边的词摆在一起比，有出入就列出来并以非零码退出。

`check.py` 里那几张词表是从 `references/words.md`（喂给模型的用词红线文档）
手抄过来的，两边各自能改。改了一边忘了另一边，就会出现「提示词里禁了但质检
不拦」或者反过来「质检拦了但提示词没说」。这个脚本就是抓这种漂移。

只比纯词表那几类。翻案腔和名词化在 `check.py` 里是正则，words.md 里没有对应
的词表，比不了。

    python scripts/test_wordlists.py
"""

import re
import sys

import check
from config import ROOT


# ---------- 配置区 ----------

# check.py 的词表对应 words.md 里的哪一节。节标题要和 md 里一字不差。
LIST_PAIRS = [
    (check.JARGON, "### 绝对禁用"),
    (check.CONTEXT_JARGON, "### 看语境"),
    (check.HARD_STOPS, "### 硬停词"),
    (check.ROAD_SIGNS, "### 模型路标"),
]

# AI 自称那几条是行为禁令，写在 prompts/system.md 的硬禁令速查里，不在 words.md。
SKIP_WORDS = {
    "作为AI", "作为一个AI", "我是一个语言模型", "我是AI", "我没有情感", "作为人工智能",
}


def Extract_Section(text: str, heading: str) -> str:
    """
    从 words.md 全文里抠出某一节的正文。

    从标题往后一直取到下一个同级或更高级的标题为止，没有下一个标题就取到文件末尾。

    Args:
        text: words.md 全文。
        heading: 节标题，例如 "### 硬停词"。
    Returns:
        这一节的正文。
    """

    start_index = text.index(heading) + len(heading)
    rest_text = text[start_index:]

    # 下一个 # 到 ### 开头的行就是下一节的起点
    stop_match = re.search(r"\n#{1,3} ", rest_text)
    return rest_text[:stop_match.start()] if stop_match else rest_text


def Main() -> int:
    """
    两边对比，有出入就列出来。

    Returns:
        对得上返回 0，对不上返回 1，可以直接当退出码。
    """

    words_text = (ROOT / "references" / "words.md").read_text(encoding = "utf-8")

    # 禁词那一节没有三级小标题，整个「一、禁词」大节都是它的范围
    forbidden_zone = words_text[
        words_text.index("## 一、禁词"):words_text.index("## 二、企业黑话")
    ]

    problems = []

    for one_word in check.FORBIDDEN:
        if one_word not in forbidden_zone:
            problems.append(f"check.FORBIDDEN 有「{one_word}」，words.md 的禁词一节里没有")

    for word_list, heading_text in LIST_PAIRS:
        section_text = Extract_Section(words_text, heading_text)
        for one_word in word_list:
            if one_word not in section_text:
                problems.append(f"check 的词表有「{one_word}」，words.md 的{heading_text}里没有")

    # AI 自称本来就不该在 words.md 里，只确认没有人往这张表里加新词
    for one_word in check.AI_SELF:
        if one_word not in SKIP_WORDS:
            problems.append(f"check.AI_SELF 有「{one_word}」，不在跳过名单里，确认一下")

    if problems:
        print(f"词表对不上，{len(problems)} 处：")
        for one_problem in problems:
            print(f"  {one_problem}")
        return 1

    checked_total = sum(len(one_list) for one_list, _ in LIST_PAIRS) + len(check.FORBIDDEN)
    print(f"词表一致，核对了 {checked_total} 个词")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "scripts"))
    sys.exit(Main())
