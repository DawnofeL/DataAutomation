#!/usr/bin/env python3
"""核对 check.py 的词表和 references/words.md 对不对得上。

    python scripts/test_wordlists.py

check.py 的词表是从 words.md 手抄的，两边各自能改，改了一边忘另一边就会
出现「提示词里禁了但质检不拦」或者反过来。这个脚本把两边摆在一起比。

只比纯词表那几类（禁词、企业黑话、硬停词、模型路标、AI 自称）。翻案腔和
名词化是正则，words.md 里没有对应的词表，不比。
"""

import re
import sys

import check
from config import ROOT

# check.py 的词表和 words.md 里对应哪一节。
# 节名是 words.md 里 ** ** 包起来的小标题，或者 ## 开头的标题。
PAIRS = [
    (check.JARGON, "### 绝对禁用"),
    (check.CONTEXT_JARGON, "### 看语境"),
    (check.HARD_STOPS, "### 硬停词"),
    (check.ROAD_SIGNS, "### 模型路标"),
]

# 这些词在 words.md 里以别的形态出现，或者本来就不该在 words.md 里，跳过。
SKIP = {
    # AI 自称那几条是行为禁令，写在 system.md 的硬禁令速查里
    "作为AI", "作为一个AI", "我是一个语言模型", "我是AI", "我没有情感", "作为人工智能",
}


def section(text, heading):
    """从 words.md 里抠出某一节的正文。

    Args:
        text (str): words.md 全文。
        heading (str): 节标题，例如 "### 硬停词"。

    Returns:
        str: 这一节到下一个同级或更高级标题之前的内容。
    """
    start = text.index(heading) + len(heading)
    rest = text[start:]
    stop = re.search(r"\n#{1,3} ", rest)
    return rest[:stop.start()] if stop else rest


def main():
    """两边对比，有出入就列出来并以非零码退出。"""
    words = (ROOT / "references" / "words.md").read_text(encoding="utf-8")
    forbidden_zone = words[words.index("## 一、禁词"):words.index("## 二、企业黑话")]

    problems = []

    for word in check.FORBIDDEN:
        if word not in forbidden_zone:
            problems.append(f"check.FORBIDDEN 有「{word}」，words.md 的禁词一节里没有")

    for wordlist, heading in PAIRS:
        body = section(words, heading)
        for word in wordlist:
            if word not in body:
                problems.append(f"check 的词表有「{word}」，words.md 的{heading}里没有")

    for word in check.AI_SELF:
        if word in SKIP:
            continue
        problems.append(f"check.AI_SELF 有「{word}」，不在跳过名单里，确认一下")

    if problems:
        print(f"词表对不上，{len(problems)} 处：")
        for p in problems:
            print(f"  {p}")
        return 1

    total = sum(len(x) for x, _ in PAIRS) + len(check.FORBIDDEN)
    print(f"词表一致，核对了 {total} 个词")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "scripts"))
    sys.exit(main())
