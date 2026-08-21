#!/usr/bin/env python3
"""终端排版的常量：宽度、线条、颜色。

只有这个文件知道具体用什么符号什么色号。想换风格改这里，别的文件不用动。
"""

import os
import sys

# 面板总宽度，按显示宽度算（一个汉字占 2）。
# 80 列终端里两边各留几格，不顶到边。
WIDTH = 72

# 左边统一缩进两格，整屏不贴着屏幕边缘。
INDENT = "  "

# 面板的框线。
TOP = "┌"
BOTTOM = "└"
SIDE = "│"
LINE = "─"

# 进度条的实心和空心。
FULL = "█"
EMPTY = "░"

# 状态标记。
OK = "✓"
BAD = "✗"


def _enable_windows_ansi():
    """让 Windows 的 cmd.exe 认 ANSI 转义码。

    Windows Terminal 和 PowerShell 7 本来就认，老 cmd.exe 要手动开
    ENABLE_VIRTUAL_TERMINAL_PROCESSING。开不了就当没有颜色。

    Returns:
        bool: 开成功或者本来就支持返回 True。
    """
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # -11 是 STD_OUTPUT_HANDLE，4 是 ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return bool(kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7))
    except Exception:
        return False


def color_on():
    """这个终端要不要上色。

    重定向到文件时不上色，免得日志里全是转义码。设了 NO_COLOR 也不上色，
    这是个跨工具的通行约定。

    Returns:
        bool: 上色返回 True。
    """
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    return _enable_windows_ansi()


COLOR = color_on()

# 色号。不上色时全部换成空串，调用处不用写 if。
_CODES = {
    "dim": "\033[2m",
    "bold": "\033[1m",
    "green": "\033[32m",
    "red": "\033[31m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
    "reset": "\033[0m",
}
CODES = _CODES if COLOR else {k: "" for k in _CODES}
