#!/usr/bin/env python3
"""读配置。run.py / parse.py / check.py / usage.py 都从这里拿。

三层，后面的盖前面的：

    config.yaml       进仓库，只放不敏感的参数
    secrets.yaml      不进仓库（.gitignore 挡着），放 api_key
    环境变量           DEEPSEEK_API_KEY，优先级最高

api_key 绝对不要写进 config.yaml。这个仓库是公开的，公开仓库里的 key
几分钟内就会被爬虫扫走盗刷。

llm.py 不读配置，cfg 由调用方传进去。
"""

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

# 存放 api_key 的本地文件，不进仓库。
SECRETS = ROOT / "secrets.yaml"

# 从这个环境变量读 api_key，优先级高于 secrets.yaml。
KEY_ENV = "DEEPSEEK_API_KEY"


def load():
    """读 config.yaml，叠上 secrets.yaml，再叠上环境变量。

    Returns:
        dict: 合并后的配置。没有任何一层提供 api_key 时，该键不存在，
            由调用方决定怎么报错。
    """
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    if SECRETS.exists():
        cfg.update(yaml.safe_load(SECRETS.read_text(encoding="utf-8")) or {})
    if os.environ.get(KEY_ENV):
        cfg["api_key"] = os.environ[KEY_ENV]
    return cfg
