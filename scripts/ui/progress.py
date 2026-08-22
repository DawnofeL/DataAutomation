"""跑的时候钉在屏幕底部、原地刷新的那几行。

本文件定义两个函数和一个类：

- `Draw_Bar`：画方块进度条，纯函数，只管拼字符串不管刷新。
- `Live`：占住屏幕底部固定几行，每次 `Update` 把这几行整块重画。
  它内部有 `Update` 和 `Close` 两个方法。
- `_Strip_Ansi`：剥掉 ANSI 转义码，只在 `Live.Update` 往文件里写的时候用。

终端里这几行原地更新，屏幕不往下滚。输出重定向到文件时退化成一步一行，
免得日志里全是回车符和光标移动码。
"""

import sys

from . import theme
from .blocks import Dim


def Draw_Bar(done: int, total: int, cells: int = 24) -> str:
    """
    画一条方块进度条。

    已完成的部分用实心块，剩下的部分用灰色空心块。总数为 0 时按 1 算，
    避免除零。

    Args:
        done: 已完成的数量。
        total: 总数。
        cells: 进度条一共占几格。
    Returns:
        形如 "████████░░░░░░░░" 的字符串，未完成部分带灰色转义码。
    """

    filled_cells = round(cells * done / max(1, total))
    rest_cells = cells - filled_cells

    # 满格时不发那对空的灰色转义码，屏幕上会多出两个看不见但占字节的记号
    return theme.BAR_FULL * filled_cells + (Dim(theme.BAR_EMPTY * rest_cells) if rest_cells else "")


class Live:
    """
    屏幕底部固定高度的一块，每次更新整块重画。

    用法：

        live_block = Live(2)
        live_block.Update(["生成  ███░░░  3/6", "用量  输入 2 万"])
        live_block.Close()

    每次 `Update` 传的行数要和建对象时的 `height` 一致，多了会被截断，少了补空行。
    对不上的话光标上移的行数就会错，画面会叠在一起。
    """

    def __init__(self, height: int):
        """
        Args:
            height: 这一块占屏幕上几行。
        """

        self.height = height

        # 输出接的是终端才能原地刷新，接文件就退化成一步一行
        self.is_live = sys.stdout.isatty()

        # 第一次画的时候光标还在这一块下面，不需要上移，画过之后才需要
        self.has_drawn = False

    def Update(self, lines: list) -> None:
        """
        把这一块整个重画一遍。

        Args:
            lines: 每一行的内容，长度应该等于建对象时的 `height`。
        """

        # 先补齐再截断，保证行数一定等于 height，不然下面上移的行数会对不上
        lines = (list(lines) + [""] * self.height)[:self.height]

        # 非终端只留第一行，一步打一行往下走，不发任何光标控制码
        if not self.is_live:
            print(f"{theme.INDENT}{_Strip_Ansi(lines[0])}")
            return

        # \033[nA 是把光标往上挪 n 行，回到这一块的顶上准备重画
        if self.has_drawn:
            sys.stdout.write(f"\033[{self.height}A")

        # \r 回到行首，\033[K 清掉这一行剩下的字符，免得上一次更长的内容残留在后面
        for one_line in lines:
            sys.stdout.write(f"\r{theme.INDENT}{one_line}\033[K\n")

        sys.stdout.flush()
        self.has_drawn = True

    def Close(self) -> None:
        """收尾。终端里这一块留在原地，光标此时已经在它下面了。"""

        if self.is_live:
            sys.stdout.flush()


def _Strip_Ansi(text: str) -> str:
    """
    剥掉文本里的 ANSI 转义码。

    输出被重定向到文件时用，日志里不该出现上色用的那些转义字符。

    Args:
        text: 可能带颜色码的文本。
    Returns:
        纯文本。
    """

    # 和 blocks.Display_Width 里同一套扫法：遇到 \033 进入跳过状态，跳到字母 m 结束
    plain_chars = []
    in_escape = False
    for one_char in text:
        if one_char == "\033":
            in_escape = True
        elif in_escape:
            in_escape = one_char != "m"
        else:
            plain_chars.append(one_char)

    return "".join(plain_chars)
