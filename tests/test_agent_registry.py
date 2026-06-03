"""provider 注册表的可扩展性测试 — 验证"加模型不改 get_provider 逻辑".

不触网 / 不需 key:用假 builder + monkeypatch config,只验注册表路由与兜底。
"""
from __future__ import annotations

from codev_platform.agent.brain import registry as reg
from codev_platform.agent.brain.base import LLMProvider


class _Fake(LLMProvider):
    def __init__(self, name, model, base_url):
        self.name, self.model, self.base_url = name, model, base_url

    def chat(self, system, messages, tools):  # pragma: no cover
        raise NotImplementedError


def test_builtin_providers_registered():
    names = reg.registered_names()
    for n in ("claude", "gpt", "deepseek", "qwen"):
        assert n in names


def test_register_new_provider_then_build(monkeypatch):
    # 注册一个全新 provider,只加一条声明,不碰 get_provider
    reg.register_provider(reg.ProviderSpec(
        name="acme", key_env="ACME_API_KEY",
        builder=lambda key, model, base, name: _Fake(name, model, base),
        default_model="acme-1", default_base_url="https://acme.test",
    ))
    cfg = {"agent": {"provider": "acme", "providers": {"acme": {"api_key": "k"}}}}
    p = reg.get_provider(cfg)
    assert p.name == "acme" and p.model == "acme-1" and p.base_url == "https://acme.test"


def test_loop_policy_spec_default_config_override_and_global():
    # 每模型策略:spec 内置默认 ⊕ config 覆盖 ⊕ 全局默认(加模型/调参不碰 loop 核心)。
    # deepseek 内置 per_tool_cap=3(弱模型调紧)
    p = reg.loop_policy({"agent": {"provider": "deepseek"}}, "deepseek")
    assert p.per_tool_cap == 3 and p.max_steps == 12
    # config 每字段覆盖优先(per-provider loop 块)
    cfg = {"agent": {"provider": "deepseek",
                     "providers": {"deepseek": {"loop": {"per_tool_cap": 7, "max_steps": 20}}}}}
    p2 = reg.loop_policy(cfg, "deepseek")
    assert p2.per_tool_cap == 7 and p2.max_steps == 20
    # 无内置档的 provider → 全局 LoopPolicy 默认;legacy agent.max_steps 仍被尊重
    p3 = reg.loop_policy({"agent": {"provider": "gpt", "max_steps": 9}}, "gpt")
    assert p3.per_tool_cap == 4 and p3.max_steps == 9


def test_unregistered_with_base_url_falls_back_to_openai_compat():
    # 未注册的厂商,只要 config 配了 base_url,就按 OpenAI 兼容自动兜底(零改代码)
    cfg = {"agent": {"provider": "newvendor",
                     "providers": {"newvendor": {"base_url": "https://x.test", "model": "m", "api_key": "k"}}}}
    spec = reg._spec_for("newvendor", cfg)
    assert spec.openai_compatible and spec.builder is reg._build_openai_compat


def test_unknown_provider_without_base_url_raises():
    cfg = {"agent": {"provider": "ghost", "providers": {}}}
    try:
        reg._spec_for("ghost", cfg)
    except RuntimeError:
        return
    raise AssertionError("未知且无 base_url 的 provider 应报错")


def test_missing_key_raises():
    cfg = {"agent": {"provider": "deepseek", "providers": {"deepseek": {"api_key": ""}}}}
    try:
        reg.get_provider(cfg)
    except RuntimeError as e:
        assert "API key" in str(e)
        return
    raise AssertionError("缺 key 应报错")
