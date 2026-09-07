"""core.obslog 脱敏纯函数 + 日志模式推断测试 (生产默认脱敏)。

obslog 已接到三套 MCP server (chroma _config/_tools, graph/server, codegraph/server)
的 usage/recall 日志路径, 但 logging_mode 的自动推断 (token 模式 → prod) 与
redact_text / redact_args 一直无单测。这里补齐 —— 守护"生产默认脱敏"语义不回退。
"""
from __future__ import annotations


from codev_platform.core.obslog import logging_mode, redact_args, redact_text


# ---------------------------------------------------------------- logging_mode
def test_default_dev_when_nothing_set(monkeypatch):
    monkeypatch.delenv("CODEV_PLATFORM_LOG_MODE", raising=False)
    assert logging_mode(None) == "dev"
    assert logging_mode({}) == "dev"


def test_token_auth_infers_prod(monkeypatch):
    """token 鉴权 (对外场景) 未显式配 logging.mode 时, 安全默认推断为 prod。"""
    monkeypatch.delenv("CODEV_PLATFORM_LOG_MODE", raising=False)
    cfg = {"gateway": {"auth_mode": "token"}}
    assert logging_mode(cfg) == "prod"


def test_non_token_auth_stays_dev(monkeypatch):
    monkeypatch.delenv("CODEV_PLATFORM_LOG_MODE", raising=False)
    assert logging_mode({"gateway": {"auth_mode": "none"}}) == "dev"


def test_explicit_dev_overrides_token_inference(monkeypatch):
    """显式 logging.mode=dev 是调试逃生口: 即便 token 模式也保留全量。"""
    monkeypatch.delenv("CODEV_PLATFORM_LOG_MODE", raising=False)
    cfg = {"gateway": {"auth_mode": "token"}, "logging": {"mode": "dev"}}
    assert logging_mode(cfg) == "dev"


def test_explicit_prod_via_config(monkeypatch):
    monkeypatch.delenv("CODEV_PLATFORM_LOG_MODE", raising=False)
    assert logging_mode({"logging": {"mode": "prod"}}) == "prod"


def test_env_overrides_config(monkeypatch):
    monkeypatch.setenv("CODEV_PLATFORM_LOG_MODE", "prod")
    # config 说 dev, env 说 prod → env 赢
    assert logging_mode({"logging": {"mode": "dev"}}) == "prod"


def test_invalid_mode_falls_back_to_inference(monkeypatch):
    monkeypatch.delenv("CODEV_PLATFORM_LOG_MODE", raising=False)
    # 非法值不静默清空日志; 归一到自动推断 (此处无 token → dev)
    assert logging_mode({"logging": {"mode": "garbage"}}) == "dev"
    # 非法值 + token → prod (推断仍生效)
    assert logging_mode({"logging": {"mode": "auto"}, "gateway": {"auth_mode": "token"}}) == "prod"


# ----------------------------------------------------------------- redact_text
def test_redact_text_dev_passthrough():
    assert redact_text("select * from users", "dev") == "select * from users"
    assert redact_text(None, "dev") is None


def test_redact_text_prod_masks_but_keeps_len_and_stable_hash():
    s = "secret query about customer phone"
    out = redact_text(s, "prod")
    assert s not in out
    assert out.startswith(f"<redacted:len={len(s)}:")
    # 稳定哈希: 同一文本两次脱敏结果一致 (便于关联 query 而不泄漏内容)
    assert redact_text(s, "prod") == out
    # 不同文本 → 不同哈希
    assert redact_text(s + "x", "prod") != out


def test_redact_text_prod_empty_and_nonstr():
    assert redact_text("", "prod") == "<redacted:len=0>"
    assert redact_text(None, "prod") is None
    assert redact_text(123, "prod") == 123  # 非 str 结构占位不脱敏


# ----------------------------------------------------------------- redact_args
def test_redact_args_dev_passthrough():
    d = {"query": "x", "k": 5}
    assert redact_args(d, "dev") == d


def test_redact_args_prod_masks_values_keeps_keys():
    out = redact_args({"query": "find table users", "table": "users", "k": 5}, "prod")
    # key 保留 (固定参数名非敏感)
    assert set(out.keys()) == {"query", "table", "k"}
    # str 值脱敏
    assert "find table users" not in str(out["query"])
    assert "users" not in str(out["table"])
    # 非 str 标量原样
    assert out["k"] == 5


def test_redact_args_prod_nondict_passthrough():
    assert redact_args(["a", "b"], "prod") == ["a", "b"]
