"""读配置。

本文件只定义一个函数：

- `Load_Config`：把根目录的 `config.yaml`、`secrets.yaml` 和环境变量三层叠起来，
  返回一个字典给别的脚本用。

`run.py`、`parse.py`、`check.py`、`usage.py` 这四个能单独启动的脚本各自调一次
`Load_Config`，`llm.py` 不读配置，它的 `cfg` 参数由调用方传进去。
"""

import os
from pathlib import Path

import yaml


# ---------- 配置区 ----------

# 项目根目录，也就是 scripts/ 的上一级。别的文件也从这里 import ROOT 用。
ROOT = Path(__file__).resolve().parent.parent

# 装 api_key 的本地文件。它写在 .gitignore 里，不进仓库，git pull 不会覆盖。
SECRETS_PATH = ROOT / "secrets.yaml"

# 从这个环境变量读 api_key，优先级比上面两个文件都高。
KEY_ENV_NAME = "DEEPSEEK_API_KEY"


def Load_Config() -> dict:
    """
    读出这一趟要用的全部配置。

    分三层叠加，后面的盖前面的：`config.yaml` 提供所有不敏感的参数（模型名、
    并发数、轮数区间这些），`secrets.yaml` 补上 `api_key`，环境变量
    `DEEPSEEK_API_KEY` 优先级最高。api_key 绝对不要写进 `config.yaml`，
    那个文件进仓库，公开仓库里的 key 几分钟就会被爬虫扫走。

    Returns:
        合并后的配置字典。三层都没提供 api_key 时字典里就没有这个键，
        由调用方自己决定怎么报错。
    """

    # config.yaml 是必须存在的那一份，读不到就让它直接抛错，不做兜底
    config_data = yaml.safe_load((ROOT / "config.yaml").read_text(encoding = "utf-8"))

    # secrets.yaml 可有可无，存在就把里面的键覆盖上去；空文件 safe_load 返回 None，用 or {} 挡掉
    if SECRETS_PATH.exists():
        config_data.update(yaml.safe_load(SECRETS_PATH.read_text(encoding = "utf-8")) or {})

    # 环境变量放最后，临时换 key 跑一趟时不用动任何文件
    if os.environ.get(KEY_ENV_NAME):
        config_data["api_key"] = os.environ[KEY_ENV_NAME]

    return config_data
