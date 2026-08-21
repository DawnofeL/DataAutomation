#!/usr/bin/env python3
"""静态排版：面板、键值行、表格、上色。

对齐一律按显示宽度算，不按字符数。一个汉字在等宽终端里占两格，用 len()
对齐中文会歪，这是这个文件存在的主要理由。
"""

import unicodedata

from . import theme


# ====================================================================
#  宽度
# ====================================================================


def width(text):
    """一段文本在等宽终端里占几格。

    东亚宽字符和全角字符按 2 算，其余按 1 算。文本里带 ANSI 转义码时
    先剥掉，转义码本身不占位置。

    Args:
        text (str): 任意文本，可以带颜色码。

    Returns:
        int: 显示宽度。
    """
    plain, skip = [], False
    for ch in text:
        if ch == "\033":
            skip = True
        elif skip:
            skip = ch not in "m"
        else:
            plain.append(ch)
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in plain)


def pad(text, cells, align="left"):
    """按显示宽度把文本补到指定格数。

    Args:
        text (str): 要补齐的文本。
        cells (int): 目标格数。文本本来就超了就原样返回，不截断。
        align (str): "left" 左对齐，"right" 右对齐。

    Returns:
        str: 补齐后的文本。
    """
    gap = max(0, cells - width(text))
    return (" " * gap + text) if align == "right" else (text + " " * gap)


# ====================================================================
#  上色
# ====================================================================


def paint(text, name):
    """给文本套一个颜色。终端不支持时原样返回。

    Args:
        text (str): 文本。
        name (str): theme.CODES 里的键，例如 "dim"、"green"。

    Returns:
        str: 带转义码的文本。
    """
    return f"{theme.CODES[name]}{text}{theme.CODES['reset']}"


def dim(text):
    """次要信息，灰一点。"""
    return paint(text, "dim")


def ok(text):
    """成功，绿色。"""
    return paint(text, "green")


def bad(text):
    """失败，红色。"""
    return paint(text, "red")


def warn(text):
    """警告，黄色。"""
    return paint(text, "yellow")


def key(text):
    """强调，青色。"""
    return paint(text, "cyan")


# ====================================================================
#  面板
# ====================================================================


def banner(text):
    """整屏最上面那一行标题。

    Args:
        text (str): 标题文字。
    """
    print(f"\n{theme.INDENT}{paint(text, 'bold')}")


def panel(title, rows):
    """一个带框的段落。

    上边框自带标题，下边框只有一个角，不封口——封死的框在终端里显得笨重，
    开口的框读起来更轻。

    Args:
        title (str): 框顶上的标题。
        rows (list[str]): 每行的内容，已经排好版的字符串。空串输出一个空行。
    """
    head = f"{theme.TOP}{theme.LINE} {title} "
    print(f"\n{theme.INDENT}{dim(head + theme.LINE * max(0, theme.WIDTH - width(head)))}")
    for row in rows:
        print(f"{theme.INDENT}{dim(theme.SIDE)} {row}" if row else f"{theme.INDENT}{dim(theme.SIDE)}")
    print(f"{theme.INDENT}{dim(theme.BOTTOM)}")


def kv(label, value, note="", label_cells=10):
    """一行「标签  值  灰色备注」。

    Args:
        label (str): 左边的标签。
        value (str): 中间的值。
        note (str): 右边的灰色补充，可以不给。
        label_cells (int): 标签占几格，同一个面板里传一样的值才对得齐。

    Returns:
        str: 排好的一行，喂给 panel()。
    """
    line = f"{pad(label, label_cells)}{value}"
    return f"{line}  {dim(note)}" if note else line


def table(rows, aligns=None, gap=2):
    """把二维文本排成对齐的表格。

    Args:
        rows (list[list[str]]): 每行每列的文本。
        aligns (list[str] | None): 每列 "left" 或 "right"，不给全部左对齐。
        gap (int): 列之间空几格。

    Returns:
        list[str]: 排好的每一行，喂给 panel()。
    """
    if not rows:
        return []
    n = max(len(r) for r in rows)
    aligns = aligns or ["left"] * n
    cells = [max(width(r[i]) for r in rows if i < len(r)) for i in range(n)]
    out = []
    for row in rows:
        parts = [pad(cell, cells[i], aligns[i]) for i, cell in enumerate(row)]
        out.append((" " * gap).join(parts).rstrip())
    return out


def num(value):
    """给大数字加千分位，读起来不用数零。

    Args:
        value (int | float): 数值。

    Returns:
        str: 形如 "6,581"。
    """
    return f"{int(value):,}"


def rule():
    """一条通栏的浅色分隔线。"""
    print(f"{theme.INDENT}{dim(theme.LINE * theme.WIDTH)}")
