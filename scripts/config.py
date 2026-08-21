#!/usr/bin/env python3
"""读 config.yaml。run.py / parse.py / check.py / usage.py 都从这里拿配置。

llm.py 不读配置，cfg 由调用方传进去。
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load():
    """读根目录的 config.yaml。

    Returns:
        dict: config.yaml 的全部内容。
    """
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
