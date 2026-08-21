#!/usr/bin/env python3
"""跑的时候钉在屏幕底部、原地刷新的那几行。

    bar()   画方块串，纯函数，不管刷新
    Live    占住固定几行，每次 update 全部重画

终端里这几行原地更新，屏幕不往下滚。重定向到文件时退化成一步一行，
免得日志里全是回车符和光标移动码。
"""

import sys

from . import theme
from .blocks import dim


def bar(done, total, cells=24):
    """画一条方块进度条。

    Args:
        done (int): 已完成数。
        total (int): 总数。
        cells (int): 进度条占几格。

    Returns:
        str: 形如 "████████░░░░░░░░"，未完成部分是灰的。
    """
    filled = round(cells * done / max(1, total))
    rest = cells - filled
    return theme.FULL * filled + (dim(theme.EMPTY * rest) if rest else "")


class Live:
    """屏幕底部固定高度的一块，原地重画。

    用法：

        live = Live(2)
        live.update(["生成  ███░░░  3/6", "用量  输入 2 万"])
        live.close()

    每次 update 传的行数要和 height 一致，多了截断，少了补空行，
    不然光标上移的行数会对不上，画面会错位。
    """

    def __init__(self, height):
        """
        Args:
            height (int): 这一块占几行。
        """
        self.height = height
        self.live = sys.stdout.isatty()
        self.drawn = False

    def update(self, lines):
        """把这一块重画一遍。

        Args:
            lines (list[str]): 每行的内容，长度应等于 height。
        """
        lines = (list(lines) + [""] * self.height)[:self.height]

        if not self.live:
            # 非终端只留第一行，一步一行往下走
            print(f"{theme.INDENT}{_strip(lines[0])}")
            return

        if self.drawn:
            sys.stdout.write(f"\033[{self.height}A")   # 光标回到这一块的顶上
        for line in lines:
            sys.stdout.write(f"\r{theme.INDENT}{line}\033[K\n")
        sys.stdout.flush()
        self.drawn = True

    def close(self):
        """收尾。终端里这一块留在原地，光标已经在它下面了。"""
        if self.live:
            sys.stdout.flush()


def _strip(text):
    """去掉 ANSI 转义码，写进文件时用。

    Args:
        text (str): 可能带颜色码的文本。

    Returns:
        str: 纯文本。
    """
    out, skip = [], False
    for ch in text:
        if ch == "\033":
            skip = True
        elif skip:
            skip = ch != "m"
        else:
            out.append(ch)
    return "".join(out)
