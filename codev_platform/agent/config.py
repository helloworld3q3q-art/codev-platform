"""agent 配置读取(复用 core.config). key 解析 env > config > 报错."""
from __future__ import annotations

from typing import Any

from codev_platform.core.config import env_or_config, get, load_config

# 注:provider 的 env 变量名 / 默认 base_url / 默认 model 等"provider 知识"
# 单一收敛到 brain/registry.py 的 ProviderSpec,本模块只做通用 config 读取,不含 provider 名表。


def agent_cfg() -> dict[str, Any]:
    return load_config()


def provider_name(cfg: dict[str, Any]) -> str:
    return get(cfg, "agent.provider", "claude")


def model_name(cfg: dict[str, Any], provider: str) -> str:
    # 顶层 agent.model 优先;否则 providers.<name>.model
    return get(cfg, "agent.model") or get(cfg, f"agent.providers.{provider}.model", "")


def max_steps(cfg: dict[str, Any]) -> int:
    return int(get(cfg, "agent.max_steps", 12))


def resolve_key(cfg: dict[str, Any], provider: str, env_var: str | None = None) -> str | None:
    """env(env_var) > config.agent.providers.<name>.api_key. 缺失返回 None(调用方报错)."""
    key = env_or_config(env_var or "", cfg, f"agent.providers.{provider}.api_key", default="")
    return key or None


def base_url(cfg: dict[str, Any], provider: str) -> str | None:
    return get(cfg, f"agent.providers.{provider}.base_url") or None


def configured_provider_names(cfg: dict[str, Any]) -> list[str]:
    """config.agent.providers 下声明的所有 provider 名(含内置 + 用户自加的兼容厂商)。"""
    providers = get(cfg, "agent.providers", {}) or {}
    return list(providers.keys())
