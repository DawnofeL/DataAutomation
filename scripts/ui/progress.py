#!/usr/bin/env python3
"""进度条。

跑的时候只占一行，原地刷新。重定向到文件时自动退化成一批一行的流水账，
免得日志里全是回车符。
"""

import sys

from . import theme
from .blocks import bad, dim, ok, pad, width


class Bar:
    """一条原地刷新的进度条。

    用法：

        bar = Bar(总数, "生成")
        for ...:
            bar.step("彩礼 第2批", failed=False)
        bar.close()
    """

    def __init__(self, total, label, cells=28):
        """
        Args:
            total (int): 一共几步。
            label (str): 进度条左边的名字。
            cells (int): 进度条本身占几格。
        """
        self.total = max(1, total)
        self.label = label
        self.cells = cells
        self.done = 0
        self.failed = 0
        self.live = sys.stdout.isatty()

    def _bar(self):
        """画出当前进度的方块串。

        Returns:
            str: 形如 "████████░░░░░░░░"。
        """
        filled = round(self.cells * self.done / self.total)
        rest = self.cells - filled
        return theme.FULL * filled + (dim(theme.EMPTY * rest) if rest else "")

    def step(self, note, failed=False):
        """走一步，重画。

        Args:
            note (str): 这一步在干什么，显示在进度条右边。
            failed (bool): 这一步是失败的。
        """
        self.done += 1
        self.failed += bool(failed)
        mark = bad(theme.BAD) if failed else ok(theme.OK)
        counter = f"{self.done}/{self.total}"
        line = (f"{theme.INDENT}{self.label}  {self._bar()}  "
                f"{pad(counter, 7)}{mark} {note}")

        if self.live:
            # \033[K 清掉这一行剩下的字符，免得上一条更长的残留在后面
            sys.stdout.write("\r" + line + "\033[K")
            sys.stdout.flush()
        else:
            print(f"{theme.INDENT}[{counter}] {'✗' if failed else '✓'} {note}")

    def close(self):
        """收尾，把光标挪到下一行。"""
        if self.live:
            sys.stdout.write("\n")
            sys.stdout.flush()
