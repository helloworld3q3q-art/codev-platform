"""Web Backend 专属配置 —— 端口等 (plan §二)。

复用 core.config.load_config() 读 ~/.codev-platform/config.json, web 专属键归在 web.* 下。
启动不依赖 DB (PG 是数据面硬依赖, 但 app 装配本身只读 config)。
"""
from __future__ import annotations

from codev_platform.core.config import get as _cfg_get
from codev_platform.core.config import load_config

DEFAULT_PORT = 18088


def web_host(cfg: dict | None = None) -> str:
    return str(_cfg_get(cfg or load_config(), "web.host", "127.0.0.1"))


def web_port(cfg: dict | None = None) -> int:
    return int(_cfg_get(cfg or load_config(), "web.port", DEFAULT_PORT))
