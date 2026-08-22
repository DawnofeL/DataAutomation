"""静态排版：算宽度、上色、画面板和表格。

本文件按三组定义函数，后面一组用前面一组：

宽度组

- `Display_Width`：算一段文本在等宽终端里占几格，是整个文件的地基。
- `Pad_To_Width`：按显示宽度把文本补齐到指定格数，内部调 `Display_Width`。

上色组

- `Paint`：给文本套一个颜色，其余五个都是它的快捷方式。
- `Dim` `Ok` `Bad` `Warn` `Key`：分别对应灰、绿、红、黄、青。

排版组

- `Banner`：整屏最上面那行标题。
- `Panel`：画一个带标题的框，业务脚本主要用它。
- `Key_Value`：排一行「标签 值 灰色备注」，结果喂给 `Panel`。
- `Table`：把二维文本排成对齐的表格，结果也喂给 `Panel`。
- `Format_Number`：给大数字加千分位。
- `Rule`：画一条通栏分隔线。

对齐一律按显示宽度算而不是字符数。一个汉字在等宽终端里占两格，用 `len()`
对齐中文会歪，这是这个文件存在的主要理由。
"""

import unicodedata

from . import theme


# ---------- 宽度 ----------


def Display_Width(text: str) -> int:
    """
    算一段文本在等宽终端里占几格。

    东亚宽字符和全角字符按 2 格算，其余按 1 格算。文本里如果带着 ANSI 转义码
    （上色用的那些 `\\033[32m`），先把它们剥掉再数，因为转义码本身不占屏幕位置。

    Args:
        text: 任意文本，可以带颜色码。
    Returns:
        这段文本占的格子数。
    """

    # 一边扫一边剥转义码：遇到 \033 就进入跳过状态，一直跳到字母 m 结束
    plain_chars = []
    in_escape = False
    for one_char in text:
        if one_char == "\033":
            in_escape = True
        elif in_escape:
            in_escape = one_char != "m"
        else:
            plain_chars.append(one_char)

    # east_asian_width 返回 W 表示宽字符、F 表示全角，这两类占两格，其余占一格
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in plain_chars)


def Pad_To_Width(text: str, cells: int, align: str = "left") -> str:
    """
    按显示宽度把文本补空格补到指定格数。

    文本本身已经超过目标格数时原样返回，不截断，宁可这一行长出去也不要把内容切掉。

    Args:
        text: 要补齐的文本。
        cells: 目标格数。
        align: "left" 补在右边（左对齐），"right" 补在左边（右对齐）。
    Returns:
        补齐后的文本。
    """

    gap_cells = max(0, cells - Display_Width(text))
    return (" " * gap_cells + text) if align == "right" else (text + " " * gap_cells)


# ---------- 上色 ----------


def Paint(text: str, color_name: str) -> str:
    """
    给一段文本套上颜色。

    终端不支持颜色时 `theme.CODES` 里全是空串，套完还是原文，调用处不用判断。

    Args:
        text: 要上色的文本。
        color_name: `theme.CODES` 里的键，例如 "dim"、"green"。
    Returns:
        前后加了转义码的文本。
    """

    return f"{theme.CODES[color_name]}{text}{theme.CODES['reset']}"


def Dim(text: str) -> str:
    """次要信息，灰一点。

    Args:
        text: 要上色的文本。
    Returns:
        灰色文本。
    """

    return Paint(text, "dim")


def Ok(text: str) -> str:
    """成功，绿色。

    Args:
        text: 要上色的文本。
    Returns:
        绿色文本。
    """

    return Paint(text, "green")


def Bad(text: str) -> str:
    """失败，红色。

    Args:
        text: 要上色的文本。
    Returns:
        红色文本。
    """

    return Paint(text, "red")


def Warn(text: str) -> str:
    """警告，黄色。

    Args:
        text: 要上色的文本。
    Returns:
        黄色文本。
    """

    return Paint(text, "yellow")


def Key(text: str) -> str:
    """强调，青色。

    Args:
        text: 要上色的文本。
    Returns:
        青色文本。
    """

    return Paint(text, "cyan")


# ---------- 排版 ----------


def Banner(text: str) -> None:
    """
    打一行加粗的大标题，前面空一行。

    Args:
        text: 标题文字。
    """

    print(f"\n{theme.INDENT}{Paint(text, 'bold')}")


def Panel(title: str, rows: list) -> None:
    """
    画一个带标题的框，把若干行内容框进去。

    上边框自带标题，下边框只画一个左下角、不封口，封死的框在终端里显得笨重。
    每一行左边都会自动补上竖线和缩进，调用方只管传内容。

    Args:
        title: 框顶上的标题。
        rows: 每一行的内容，都是已经排好版的字符串。传空串就输出一个空行。
    """

    # 上边框是「角 + 横线 + 标题 + 横线」，横线补到 PANEL_WIDTH 那么宽
    head_text = f"{theme.BOX_TOP}{theme.BOX_LINE} {title} "
    tail_line = theme.BOX_LINE * max(0, theme.PANEL_WIDTH - Display_Width(head_text))
    print(f"\n{theme.INDENT}{Dim(head_text + tail_line)}")

    # 空串只画竖线不加空格，免得行尾留一串没用的空白
    for one_row in rows:
        if one_row:
            print(f"{theme.INDENT}{Dim(theme.BOX_SIDE)} {one_row}")
        else:
            print(f"{theme.INDENT}{Dim(theme.BOX_SIDE)}")

    print(f"{theme.INDENT}{Dim(theme.BOX_BOTTOM)}")


def Key_Value(label: str, value: str, note: str = "", label_cells: int = 10) -> str:
    """
    排一行「标签 值 灰色备注」。

    同一个面板里的几行要传一样的 `label_cells`，值那一列才对得齐。

    Args:
        label: 左边的标签。
        value: 中间的值。
        note: 右边的灰色补充说明，不需要就不传。
        label_cells: 标签占几格。
    Returns:
        排好的一行，直接喂给 `Panel`。
    """

    main_text = f"{Pad_To_Width(label, label_cells)}{value}"
    return f"{main_text}  {Dim(note)}" if note else main_text


def Table(rows: list, aligns: list = None, gap: int = 2) -> list:
    """
    把二维文本排成每一列都对齐的表格。

    先量出每一列最宽的那格有多宽，再按这个宽度补齐所有格子。行与行的列数可以
    不一样，短的那几行后面缺的列直接留空。

    Args:
        rows: 每一行是一个字符串列表，一个元素就是一格。
        aligns: 每一列是 "left" 还是 "right"，不传就全部左对齐。
        gap: 列与列之间空几格。
    Returns:
        排好的每一行，直接喂给 `Panel`。
    """

    if not rows:
        return []

    # 以最长那一行的列数为准，短行缺的列后面按空处理
    column_count = max(len(one_row) for one_row in rows)
    aligns = aligns or ["left"] * column_count

    # 逐列取所有行里最宽的那格，作为这一列的目标宽度
    column_widths = [
        max(Display_Width(one_row[i]) for one_row in rows if i < len(one_row))
        for i in range(column_count)
    ]

    # 补齐每一格再用空格拼起来，行尾多出来的空白顺手去掉
    output_lines = []
    for one_row in rows:
        padded = [Pad_To_Width(cell, column_widths[i], aligns[i]) for i, cell in enumerate(one_row)]
        output_lines.append((" " * gap).join(padded).rstrip())

    return output_lines


def Format_Number(value) -> str:
    """
    给大数字加千分位逗号，读起来不用数零。

    Args:
        value: 整数或浮点数。
    Returns:
        形如 "6,581" 的字符串。
    """

    return f"{int(value):,}"


def Rule() -> None:
    """画一条通栏的浅色分隔线。"""

    print(f"{theme.INDENT}{Dim(theme.BOX_LINE * theme.PANEL_WIDTH)}")
