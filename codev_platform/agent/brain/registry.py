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
    # 模型专用 prompt profile。None = 只用通用 prompt;config 可覆盖或置空禁用。
    # 注意:profile 文案是可复用能力档,不要为 OpenAI 兼容厂商新建适配器文件。
    default_prompt_profile: str | None = None
    # Web agent 专用规则/技能包。与 prompt profile 分开,方便按模型独立调"规则源 + 能力流程"。
    default_rule_pack: str | None = None
    default_skill_pack: str | None = None


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


# ---- 能力档默认矩阵(护栏逻辑模型无关, 参数按模型能力分档; 见 agent-loop-guard-redesign plan §六)----
# 强模型指令遵从好 / 自控强 → 少管(关 novelty + 宽 cap, 避免误伤探索);弱模型 → 严管防换词空转。
# 加模型 = 选一档进 spec(或纯 config 逐字段覆盖), loop.py 一行不动。
_STRONG = LoopPolicy(retrieval_distinct_cap=12, no_progress_limit=5,
                     novelty_check=False, readonly_total_cap=30)
_MID = LoopPolicy(retrieval_distinct_cap=8, no_progress_limit=3,
                  novelty_check=True, readonly_total_cap=25)
_WEAK = LoopPolicy(retrieval_distinct_cap=6, no_progress_limit=3,
                   novelty_check=True, readonly_total_cap=20)

# ---- 内置 provider 声明(加内置厂商在此加一行)----
register_provider(ProviderSpec("claude", "ANTHROPIC_API_KEY", _build_anthropic,
                               default_model="claude-opus-4-7",
                               default_loop_policy=_STRONG))
register_provider(ProviderSpec("gpt", "OPENAI_API_KEY", _build_openai_compat,
                               default_model="gpt-4o", default_base_url="https://api.openai.com/v1",
                               openai_compatible=True,
                               default_loop_policy=_STRONG))
register_provider(ProviderSpec("deepseek", "DEEPSEEK_API_KEY", _build_openai_compat,
                               default_model="deepseek-chat", default_base_url="https://api.deepseek.com",
                               openai_compatible=True,
                               # deepseek-chat 工具选型 / 收敛偏弱: 弱档严管, 防变参 thrash + 换词空转。
                               default_loop_policy=_WEAK,
                               default_prompt_profile="explicit_tool_selection",
                               default_rule_pack="mcp_first_code_understanding",
                               default_skill_pack="code_understanding"))
register_provider(ProviderSpec("qwen", "DASHSCOPE_API_KEY", _build_openai_compat,
                               default_model="qwen-max",
                               default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                               openai_compatible=True,
                               default_loop_policy=_MID))


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
    """解析某 provider 的循环行为档(策略)。每字段独立按优先级解析:
      config `agent.providers.<name>.loop.<f>` > `agent.loop.<f>` > (legacy 别名同两级)
      > spec.default_loop_policy.<f> > LoopPolicy() 全局默认。
    加模型 / 调参只动 config 或 spec, loop 核心零改 (agent-provider §1/§4)。
    deprecated: `per_tool_cap` 作 `retrieval_distinct_cap` 的别名(两级 config 都认), 不破存量配置。"""
    cfg = cfg or acfg.agent_cfg()
    name = name or acfg.provider_name(cfg)
    spec = _REGISTRY.get(name)
    base = (spec.default_loop_policy if spec and spec.default_loop_policy else LoopPolicy())

    def _raw(field: str, legacy_keys: tuple[str, ...] = ()) -> Any:
        # 先按 provider 维度 + global 维度找 field, 再找 legacy 别名键, 都没有则回退 base。
        for key in (f"agent.providers.{name}.loop.{field}", f"agent.loop.{field}", *legacy_keys):
            v = acfg.get(cfg, key)
            if v is not None:
                return v
        return getattr(base, field)

    def _int(field: str, legacy_keys: tuple[str, ...] = ()) -> int:
        return int(_raw(field, legacy_keys))

    def _bool(field: str) -> bool:
        v = _raw(field)
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)

    # retrieval_distinct_cap 兼容旧 per_tool_cap 别名(provider 与 global 两级)。
    _cap_legacy = (f"agent.providers.{name}.loop.per_tool_cap", "agent.loop.per_tool_cap")
    return LoopPolicy(
        max_steps=_int("max_steps", ("agent.max_steps",)),
        retrieval_distinct_cap=_int("retrieval_distinct_cap", _cap_legacy),
        no_progress_limit=_int("no_progress_limit"),
        novelty_check=_bool("novelty_check"),
        readonly_distinct_cap=_int("readonly_distinct_cap"),
        readonly_total_cap=_int("readonly_total_cap"),
        invalid_call_limit=_int("invalid_call_limit"),
        min_read_for_finish=_int("min_read_for_finish"),
    )


def _provider_setting(
    cfg: dict[str, Any], name: str, field: str, default: str | None,
) -> str | None:
    """解析 provider 可插拔配置字段。

    优先级:`agent.providers.<name>.<field>` > `agent.<field>` > provider spec 默认。
    ""/none/off/false/0 显式禁用。用于 prompt_profile/rule_pack/skill_pack。
    """
    raw = None
    for key in (f"agent.providers.{name}.{field}", f"agent.{field}"):
        v = acfg.get(cfg, key)
        if v is not None:
            raw = v
            break
    if raw is None:
        raw = default
    if raw is None:
        return None
    value = str(raw).strip()
    if not value or value.lower() in {"none", "off", "false", "0"}:
        return None
    return value


def prompt_profile(cfg: dict[str, Any] | None = None, name: str | None = None) -> str | None:
    """解析某 provider 的 prompt profile。"""
    cfg = cfg or acfg.agent_cfg()
    name = name or acfg.provider_name(cfg)
    spec = _REGISTRY.get(name)
    return _provider_setting(
        cfg, name, "prompt_profile",
        spec.default_prompt_profile if spec is not None else None,
    )


def rule_pack(cfg: dict[str, Any] | None = None, name: str | None = None) -> str | None:
    """解析某 provider 的 Web-agent 规则包。"""
    cfg = cfg or acfg.agent_cfg()
    name = name or acfg.provider_name(cfg)
    spec = _REGISTRY.get(name)
    return _provider_setting(
        cfg, name, "rule_pack",
        spec.default_rule_pack if spec is not None else None,
    )


def skill_pack(cfg: dict[str, Any] | None = None, name: str | None = None) -> str | None:
    """解析某 provider 的 Web-agent 技能包。"""
    cfg = cfg or acfg.agent_cfg()
    name = name or acfg.provider_name(cfg)
    spec = _REGISTRY.get(name)
    return _provider_setting(
        cfg, name, "skill_pack",
        spec.default_skill_pack if spec is not None else None,
    )


def rule_pack_sources(cfg: dict[str, Any] | None = None, name: str | None = None) -> Any:
    """解析 rule_pack 对应的可配置 source 列表。

    路径写在 config:
      agent.instruction_packs.rule_packs.<pack_name> = ["rules:ai-tools-mcp.md", "..."]
    未配置返回 None,由 prompts.py 使用内置 pack 兜底。
    """
    cfg = cfg or acfg.agent_cfg()
    name = name or acfg.provider_name(cfg)
    pack = rule_pack(cfg, name)
    if not pack:
        return None
    return acfg.get(cfg, f"agent.instruction_packs.rule_packs.{pack}")


def skill_pack_sources(cfg: dict[str, Any] | None = None, name: str | None = None) -> Any:
    """解析 skill_pack 对应的可配置 source 列表。

    路径写在 config:
      agent.instruction_packs.skill_packs.<pack_name> = ["builtin:code_understanding", "..."]
    未配置返回 None,由 prompts.py 使用内置 pack 兜底。
    """
    cfg = cfg or acfg.agent_cfg()
    name = name or acfg.provider_name(cfg)
    pack = skill_pack(cfg, name)
    if not pack:
        return None
    return acfg.get(cfg, f"agent.instruction_packs.skill_packs.{pack}")


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
