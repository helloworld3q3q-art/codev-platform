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


def test_loop_policy_capability_tiers():
    # 能力档默认矩阵(agent-loop-guard-redesign plan §六): 强档关 novelty + 宽 cap, 弱档严管。
    pc = reg.loop_policy({"agent": {"provider": "claude"}}, "claude")
    assert pc.novelty_check is False and pc.retrieval_distinct_cap == 12 and pc.no_progress_limit == 5
    pg = reg.loop_policy({"agent": {"provider": "gpt"}}, "gpt")
    assert pg.novelty_check is False and pg.retrieval_distinct_cap == 12
    pq = reg.loop_policy({"agent": {"provider": "qwen"}}, "qwen")
    assert pq.novelty_check is True and pq.retrieval_distinct_cap == 8 and pq.readonly_total_cap == 25
    pd = reg.loop_policy({"agent": {"provider": "deepseek"}}, "deepseek")
    assert pd.novelty_check is True and pd.retrieval_distinct_cap == 6 and pd.readonly_total_cap == 20
    assert pd.max_steps == 12  # 未覆盖字段走全局默认


def test_prompt_profile_defaults_and_overrides():
    # DeepSeek 默认启用显式工具选型 overlay;强模型默认只用通用 prompt。
    assert reg.prompt_profile({"agent": {"provider": "deepseek"}}, "deepseek") == "explicit_tool_selection"
    assert reg.prompt_profile({"agent": {"provider": "claude"}}, "claude") is None
    # provider 级配置可覆盖 / 禁用;config-only provider 也能通过配置启用 profile。
    cfg = {"agent": {"provider": "deepseek", "providers": {"deepseek": {"prompt_profile": "off"}}}}
    assert reg.prompt_profile(cfg, "deepseek") is None
    cfg_empty = {"agent": {"provider": "deepseek", "providers": {"deepseek": {"prompt_profile": ""}}}}
    assert reg.prompt_profile(cfg_empty, "deepseek") is None
    cfg2 = {"agent": {"provider": "newco", "providers": {
        "newco": {"base_url": "https://x.test", "prompt_profile": "explicit_tool_selection"}
    }}}
    assert reg.prompt_profile(cfg2, "newco") == "explicit_tool_selection"


def test_rule_and_skill_pack_defaults_and_overrides():
    # DeepSeek 默认不只是 prompt,还带 Web-agent rule/skill pack;强模型默认不额外注入。
    assert reg.rule_pack({"agent": {"provider": "deepseek"}}, "deepseek") == "mcp_first_code_understanding"
    assert reg.skill_pack({"agent": {"provider": "deepseek"}}, "deepseek") == "code_understanding"
    assert reg.rule_pack({"agent": {"provider": "claude"}}, "claude") is None
    assert reg.skill_pack({"agent": {"provider": "claude"}}, "claude") is None

    # provider 级配置可禁用;config-only 新模型也能显式选择 pack。
    cfg = {"agent": {"provider": "deepseek", "providers": {"deepseek": {
        "rule_pack": "off", "skill_pack": "",
    }}}}
    assert reg.rule_pack(cfg, "deepseek") is None
    assert reg.skill_pack(cfg, "deepseek") is None
    cfg2 = {"agent": {"provider": "newco", "providers": {"newco": {
        "base_url": "https://x.test",
        "rule_pack": "mcp_first_code_understanding",
        "skill_pack": "code_understanding",
    }}}}
    assert reg.rule_pack(cfg2, "newco") == "mcp_first_code_understanding"
    assert reg.skill_pack(cfg2, "newco") == "code_understanding"


def test_instruction_pack_sources_from_config():
    cfg = {"agent": {
        "provider": "deepseek",
        "providers": {"deepseek": {
            "rule_pack": "custom_rules",
            "skill_pack": "custom_skills",
        }},
        "instruction_packs": {
            "rule_packs": {"custom_rules": ["rules:ai-tools-mcp.md"]},
            "skill_packs": {"custom_skills": ["builtin:code_understanding"]},
        },
    }}
    assert reg.rule_pack_sources(cfg, "deepseek") == ["rules:ai-tools-mcp.md"]
    assert reg.skill_pack_sources(cfg, "deepseek") == ["builtin:code_understanding"]


def test_loop_policy_per_field_override():
    # (a) 每个新字段都能经 agent.providers.<name>.loop.<f> 逐字段覆盖(含 bool)。
    cfg = {"agent": {"provider": "deepseek", "providers": {"deepseek": {"loop": {
        "retrieval_distinct_cap": 9, "no_progress_limit": 7, "novelty_check": False,
        "readonly_distinct_cap": 33, "readonly_total_cap": 44,
        "invalid_call_limit": 5, "min_read_for_finish": 4, "max_steps": 20}}}}}
    p = reg.loop_policy(cfg, "deepseek")
    assert (p.retrieval_distinct_cap == 9 and p.no_progress_limit == 7 and p.novelty_check is False
            and p.readonly_distinct_cap == 33 and p.readonly_total_cap == 44
            and p.invalid_call_limit == 5 and p.min_read_for_finish == 4 and p.max_steps == 20)
    # 全局 agent.loop.<f> 维度(非 provider 专属)也能覆盖 bool。
    p2 = reg.loop_policy({"agent": {"provider": "qwen", "loop": {"novelty_check": False}}}, "qwen")
    assert p2.novelty_check is False


def test_loop_policy_per_tool_cap_deprecated_alias():
    # (c) per_tool_cap 别名向后兼容: 旧 config / 旧构造仍映射 retrieval_distinct_cap。
    cfg = {"agent": {"provider": "deepseek", "providers": {"deepseek": {"loop": {"per_tool_cap": 7}}}}}
    p = reg.loop_policy(cfg, "deepseek")
    assert p.retrieval_distinct_cap == 7 and p.per_tool_cap == 7
    # 全局维度别名亦认
    p2 = reg.loop_policy({"agent": {"provider": "qwen", "loop": {"per_tool_cap": 5}}}, "qwen")
    assert p2.retrieval_distinct_cap == 5 and p2.per_tool_cap == 5


def test_loop_policy_unspecced_provider_uses_global_default():
    # (d) config-only 新厂商(无 spec 档)→ 全局 LoopPolicy 裸默认, 不串到别家档位。
    cfg = {"agent": {"provider": "newco", "providers": {"newco": {"base_url": "https://x.test"}}}}
    p = reg.loop_policy(cfg, "newco")
    assert p.retrieval_distinct_cap == 8 and p.novelty_check is True and p.max_steps == 12
    # legacy agent.max_steps 仍被尊重(有 spec 的 gpt 走强档默认 cap, max_steps 从 legacy 取)。
    p2 = reg.loop_policy({"agent": {"provider": "gpt", "max_steps": 9}}, "gpt")
    assert p2.max_steps == 9 and p2.retrieval_distinct_cap == 12


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
