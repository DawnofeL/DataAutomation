#!/usr/bin/env python3
"""命令行长什么样，全在这个包里。

    theme.py      宽度、线条、颜色，想换风格只改这里
    blocks.py     面板、键值行、表格、按显示宽度对齐
    progress.py   进度条

业务脚本只调这里导出的东西，自己不拼转义码、不数空格。
"""

from .blocks import (
    bad,
    banner,
    dim,
    kv,
    key,
    num,
    ok,
    pad,
    panel,
    paint,
    rule,
    table,
    warn,
    width,
)
from .progress import Bar
from .theme import BAD, INDENT, OK, WIDTH

__all__ = [
    "bad", "banner", "dim", "kv", "key", "num", "ok", "pad",
    "panel", "paint", "rule", "table", "warn", "width",
    "Bar", "BAD", "INDENT", "OK", "WIDTH",
]
