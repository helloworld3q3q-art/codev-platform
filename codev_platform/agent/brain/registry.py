"""provider 注册表 — 声明式、可扩展、模块化.

设计目标:加一个新模型 = 加一条声明(或纯改 config),不改 get_provider 逻辑。

三种扩展姿势,从轻到重:
  1. OpenAI 兼容的新厂商(最常见)→ 纯 config 加一段(base_url+model+key),零改代码。
     get_provider 对未注册但配了 base_url 的 provider 自动按 OpenAI 兼容构建。
  2. 内置常用厂商 → register_provider(ProviderSpec(...)) 加一行声明。
  3. 全新协议(非 OpenAI/Anthropic)→ 写个 builder + register_provider 一行。

provider 知识(env 变量 / 默认 base_url / 默认 model / builder)单一收敛在此,
config.py 只做通用读取,不含 provider 名表。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from collections.abc import Callable

from codev_platform.agent import config as acfg
from codev_platform.agent.brain.base import LLMProvider
from codev_platform.agent.policy import LoopPolicy

# builder 统一签名:(api_key, model, base_url, name) -> LLMProvider
Builder = Callable[[str, str, "str | None", str], LLMProvider]


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    key_env: str                      # API key 的 env 变量名
    builder: Builder                  # 如何实例化
    default_model: str = ""
    default_base_url: str | None = None
    openai_compatible: bool = False   # True = 可作为"未注册厂商"自动兜底的同类
    # 内置行为档(每模型策略的 code 默认层, 同 default_model)。None = 用全局 LoopPolicy 默认。
    # 弱模型(指令遵从差)在此调紧; config 可再覆盖。
    default_loop_policy: LoopPolicy | None = None


_REGISTRY: dict[str, ProviderSpec] = {}


def register_provider(spec: ProviderSpec) -> None:
    """注册 provider。重复名覆盖(允许外部插件替换内置实现)。"""
    _REGISTRY[spec.name] = spec


def get_spec(name: str) -> ProviderSpec | None:
    return _REGISTRY.get(name)


def registered_names() -> list[str]:
    return list(_REGISTRY)


# ---- builders(延迟导入,没装 agent extra 时不影响其它子命令)----

def _build_anthropic(api_key: str, model: str, base_url: str | None, name: str) -> LLMProvider:
    from codev_platform.agent.brain.anthropic import AnthropicProvider
    return AnthropicProvider(api_key=api_key, model=model)


def _build_openai_compat(api_key: str, model: str, base_url: str | None, name: str) -> LLMProvider:
    from codev_platform.agent.brain.openai_compat import OpenAICompatProvider
    return OpenAICompatProvider(api_key=api_key, model=model, base_url=base_url, name=name)


# ---- 内置 provider 声明(加内置厂商在此加一行)----
register_provider(ProviderSpec("claude", "ANTHROPIC_API_KEY", _build_anthropic,
                               default_model="claude-opus-4-7"))
register_provider(ProviderSpec("gpt", "OPENAI_API_KEY", _build_openai_compat,
                               default_model="gpt-4o", default_base_url="https://api.openai.com/v1",
                               openai_compatible=True))
register_provider(ProviderSpec("deepseek", "DEEPSEEK_API_KEY", _build_openai_compat,
                               default_model="deepseek-chat", default_base_url="https://api.deepseek.com",
                               openai_compatible=True,
                               # deepseek-chat 工具选型 / 收敛偏弱: 同工具上限调紧, 防变参 thrash。
                               default_loop_policy=LoopPolicy(per_tool_cap=3)))
register_provider(ProviderSpec("qwen", "DASHSCOPE_API_KEY", _build_openai_compat,
                               default_model="qwen-max",
                               default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                               openai_compatible=True))


def _spec_for(name: str, cfg: dict[str, Any]) -> ProviderSpec:
    """取 spec;未注册但 config 配了 base_url → 当作 OpenAI 兼容厂商自动兜底(零改代码加厂商)。"""
    spec = _REGISTRY.get(name)
    if spec is not None:
        return spec
    if acfg.base_url(cfg, name):
        return ProviderSpec(
            name=name,
            key_env=f"{name.upper()}_API_KEY",
            builder=_build_openai_compat,
            openai_compatible=True,
        )
    raise RuntimeError(
        f"未知 provider: {name}。内置:{', '.join(registered_names())};"
        f"或在 config.agent.providers.{name} 配 base_url 走 OpenAI 兼容自动接入。"
    )


def get_provider(cfg: dict[str, Any] | None = None) -> LLMProvider:
    cfg = cfg or acfg.agent_cfg()
    name = acfg.provider_name(cfg)
    spec = _spec_for(name, cfg)

    key = acfg.resolve_key(cfg, name, spec.key_env)
    if not key:
        raise RuntimeError(
            f"provider '{name}' 缺少 API key:设 env {spec.key_env} 或 config.agent.providers.{name}.api_key"
        )
    model = acfg.model_name(cfg, name) or spec.default_model
    base = acfg.base_url(cfg, name) or spec.default_base_url
    try:
        return spec.builder(key, model, base, name)
    except ImportError as e:
        raise RuntimeError(f"provider '{name}' 依赖未装(pip install -e .[agent]):{e}") from e


def loop_policy(cfg: dict[str, Any] | None = None, name: str | None = None) -> LoopPolicy:
    """解析某 provider 的循环行为档(策略)。优先级 (每字段独立):
      config `agent.providers.<name>.loop.<f>` > `agent.loop.<f>` > (max_steps 兼容 `agent.max_steps`)
      > spec.default_loop_policy.<f> > LoopPolicy() 全局默认。
    加模型 / 调参只动 config 或 spec, loop 核心零改 (agent-provider §1/§4)。"""
    cfg = cfg or acfg.agent_cfg()
    name = name or acfg.provider_name(cfg)
    spec = _REGISTRY.get(name)
    base = (spec.default_loop_policy if spec and spec.default_loop_policy else LoopPolicy())

    def _pick(field: str, legacy: tuple[str, ...] = ()) -> int:
        for key in (f"agent.providers.{name}.loop.{field}", f"agent.loop.{field}", *legacy):
            v = acfg.get(cfg, key)
            if v is not None:
                return int(v)
        return int(getattr(base, field))

    return LoopPolicy(
        max_steps=_pick("max_steps", ("agent.max_steps",)),
        per_tool_cap=_pick("per_tool_cap"),
    )


def list_providers(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """给 /providers 端点:列内置 + config 声明的 provider 是否 configured(key 在位)。不返 key。"""
    cfg = cfg or acfg.agent_cfg()
    names: list[str] = list(registered_names())
    for n in acfg.configured_provider_names(cfg):  # 含用户自加的兼容厂商
        if n not in names:
            names.append(n)
    out = []
    for name in names:
        spec = _REGISTRY.get(name)
        env_var = spec.key_env if spec else f"{name.upper()}_API_KEY"
        out.append({
            "name": name,
            "model": acfg.model_name(cfg, name) or (spec.default_model if spec else ""),
            "configured": acfg.resolve_key(cfg, name, env_var) is not None,
        })
    return out
