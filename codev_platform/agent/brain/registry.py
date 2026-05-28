"""provider 工厂:按 config 选 + 校验 key + 实例化.

P0 实装 claude;gpt/deepseek/qwen 走 openai_compat(P1),未实装前给清晰报错。
"""
from __future__ import annotations

from typing import Any

from codev_platform.agent import config as acfg
from codev_platform.agent.brain.base import LLMProvider

_OPENAI_COMPAT = {"gpt", "deepseek", "qwen"}


def get_provider(cfg: dict[str, Any] | None = None) -> LLMProvider:
    cfg = cfg or acfg.agent_cfg()
    provider = acfg.provider_name(cfg)
    model = acfg.model_name(cfg, provider)
    key = acfg.resolve_key(cfg, provider)
    if not key:
        env = acfg._KEY_ENV.get(provider, "(未知)")
        raise RuntimeError(
            f"provider '{provider}' 缺少 API key:设 env {env} 或 config.agent.providers.{provider}.api_key"
        )

    if provider == "claude":
        from codev_platform.agent.brain.anthropic import AnthropicProvider
        return AnthropicProvider(api_key=key, model=model or "claude-opus-4-7")

    if provider in _OPENAI_COMPAT:
        try:
            from codev_platform.agent.brain.openai_compat import OpenAICompatProvider
        except ImportError as e:
            raise RuntimeError(f"openai_compat 适配器尚未就绪(P1):{e}") from e
        base = acfg.base_url(cfg, provider)
        return OpenAICompatProvider(api_key=key, model=model, base_url=base, name=provider)

    raise RuntimeError(f"未知 provider: {provider}(支持 claude / gpt / deepseek / qwen)")


def list_providers(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """给 /providers 端点:列各 provider 是否 configured(key 在位). 不返 key 本身."""
    cfg = cfg or acfg.agent_cfg()
    out = []
    for name in ("claude", "gpt", "deepseek", "qwen"):
        out.append({
            "name": name,
            "model": acfg.model_name(cfg, name),
            "configured": acfg.resolve_key(cfg, name) is not None,
        })
    return out
