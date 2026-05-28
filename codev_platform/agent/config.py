"""agent 配置读取(复用 core.config). key 解析 env > config > 报错."""
from __future__ import annotations

from typing import Any

from codev_platform.core.config import env_or_config, get, load_config

# provider -> (env 变量名, config 默认 base_url)
_KEY_ENV = {
    "claude": "ANTHROPIC_API_KEY",
    "gpt": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
}


def agent_cfg() -> dict[str, Any]:
    return load_config()


def provider_name(cfg: dict[str, Any]) -> str:
    return get(cfg, "agent.provider", "claude")


def model_name(cfg: dict[str, Any], provider: str) -> str:
    # 顶层 agent.model 优先;否则 providers.<name>.model
    return get(cfg, "agent.model") or get(cfg, f"agent.providers.{provider}.model", "")


def max_steps(cfg: dict[str, Any]) -> int:
    return int(get(cfg, "agent.max_steps", 12))


def resolve_key(cfg: dict[str, Any], provider: str) -> str | None:
    """env > config.agent.providers.<name>.api_key. 缺失返回 None(调用方报错)."""
    env_var = _KEY_ENV.get(provider, "")
    key = env_or_config(env_var, cfg, f"agent.providers.{provider}.api_key", default="")
    return key or None


def base_url(cfg: dict[str, Any], provider: str) -> str | None:
    return get(cfg, f"agent.providers.{provider}.base_url") or None
