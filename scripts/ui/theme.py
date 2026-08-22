"""终端排版的常量和颜色开关。

本文件定义两个函数和一批常量：

- `_Enable_Windows_Ansi`：在 Windows 上打开控制台的 ANSI 转义支持，只被
  `Color_On` 调用。
- `Color_On`：判断当前这个终端要不要上色，模块加载时调用一次，结果存进 `COLOR`。

常量分三组：`PANEL_WIDTH` `INDENT` 管尺寸，`BOX_TOP` 这几个管框线，
`CODES` 是最终能直接往字符串里塞的色号表。同一个包里的 `blocks.py` 和
`progress.py` 都从这里取值，想换整体风格只改这个文件。
"""

import os
import sys


# ---------- 配置区 ----------

# 面板总宽度，按显示宽度算（一个汉字占 2 格）。80 列终端里两边各留几格，不顶到边。
PANEL_WIDTH = 72

# 每一行左边统一缩进两格，整屏不贴着屏幕边缘。
INDENT = "  "

# 面板的框线字符。上边框带标题，下边框只画一个角，不封口。
BOX_TOP = "┌"
BOX_BOTTOM = "└"
BOX_SIDE = "│"
BOX_LINE = "─"

# 进度条的实心块和空心块。
BAR_FULL = "█"
BAR_EMPTY = "░"

# 成功和失败的标记。
MARK_OK = "✓"
MARK_BAD = "✗"

# 上色用的 ANSI 转义码。终端不支持时下面会整张换成空串。
_RAW_CODES = {
    "dim": "\033[2m",
    "bold": "\033[1m",
    "green": "\033[32m",
    "red": "\033[31m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
    "reset": "\033[0m",
}


def _Enable_Windows_Ansi() -> bool:
    """
    让 Windows 的控制台认 ANSI 转义码。

    Windows Terminal 和 PowerShell 7 本来就认，老版 cmd.exe 要手动打开一个叫
    ENABLE_VIRTUAL_TERMINAL_PROCESSING 的开关（Windows 控制台的一个模式位，
    打开之后 `\\033[32m` 这类转义码才会被当成颜色指令而不是乱码原样打出来）。
    非 Windows 系统直接返回 True，什么都不做。

    Returns:
        能用 ANSI 转义码返回 True，打不开或者出异常返回 False。
    """

    # 非 Windows 的终端天然支持，不用折腾
    if os.name != "nt":
        return True

    try:

        # ctypes 是 Python 标准库里直接调系统 DLL 的模块，这里用它调 Windows 的 kernel32
        import ctypes
        kernel32 = ctypes.windll.kernel32

        # -11 是 STD_OUTPUT_HANDLE（标准输出的句柄编号），7 是三个模式位相或的结果，其中包含虚拟终端处理
        return bool(kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7))

    except Exception:

        # 拿不到句柄、设置失败、非常规环境都归到这里，当作没有颜色处理
        return False


def Color_On() -> bool:
    """
    判断这一次运行要不要给输出上色。

    三道判断：设了 `NO_COLOR` 环境变量就不上色（这是个跨工具的通行约定），
    输出被重定向到文件时不上色（免得日志里全是转义码），Windows 上还要能成功
    打开控制台的 ANSI 支持才算数。

    Returns:
        要上色返回 True。
    """

    # NO_COLOR 是社区约定，只要这个变量非空就一律不上色
    if os.environ.get("NO_COLOR"):
        return False

    # isatty 为假说明输出接的是文件或管道，不是终端，转义码会变成脏字符
    if not sys.stdout.isatty():
        return False

    return _Enable_Windows_Ansi()


# 模块加载时判一次，后面所有地方直接用结果，不重复判断
COLOR = Color_On()

# 不上色时整张表换成空串，调用处不用到处写 if
CODES = _RAW_CODES if COLOR else {key_name: "" for key_name in _RAW_CODES}
