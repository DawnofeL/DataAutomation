"""命令行长什么样，全在这个包里。

包里三个模块，后面的用前面的：

- `theme.py`：宽度、框线字符、颜色开关。想换整体风格只改这一个文件。
- `blocks.py`：静态排版。按显示宽度对齐，画面板、键值行、表格。
- `progress.py`：动态部分。屏幕底部原地刷新的进度条和用量。

本文件自己不写逻辑，只把三个模块里业务脚本会用到的东西集中导出一遍。
`run.py`、`parse.py`、`check.py` 一律 `import ui` 之后调这里导出的名字，
自己不拼转义码、不数空格。
"""

from .blocks import (
    Bad,
    Banner,
    Dim,
    Display_Width,
    Format_Number,
    Key,
    Key_Value,
    Ok,
    Pad_To_Width,
    Paint,
    Panel,
    Rule,
    Table,
    Warn,
)
from .progress import Draw_Bar, Live
from .theme import INDENT, MARK_BAD, MARK_OK, PANEL_WIDTH

__all__ = [
    "Bad",
    "Banner",
    "Dim",
    "Display_Width",
    "Draw_Bar",
    "Format_Number",
    "INDENT",
    "Key",
    "Key_Value",
    "Live",
    "MARK_BAD",
    "MARK_OK",
    "Ok",
    "Pad_To_Width",
    "Paint",
    "PANEL_WIDTH",
    "Panel",
    "Rule",
    "Table",
    "Warn",
]
