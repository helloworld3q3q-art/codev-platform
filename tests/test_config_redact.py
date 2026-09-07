"""redact_config 纯函数掩码测试 (config doctor/show --redact)。"""
from codev_platform.cli import redact_config


def test_masks_api_key_and_password_and_token():
    cfg = {"agent": {"providers": {"deepseek": {"api_key": "sk-abcdef123456", "model": "x"}}},
           "db": {"password": "supersecretpw"}, "auth": {"token": "tok_1234567890"}}
    out = redact_config(cfg)
    assert out["agent"]["providers"]["deepseek"]["api_key"] == "sk-a***"
    assert out["agent"]["providers"]["deepseek"]["model"] == "x"  # 非敏感不动
    assert "supersecretpw" not in str(out)
    assert "tok_1234567890" not in str(out)


def test_masks_dsn_password_segment_only():
    cfg = {"memory": {"pg_dsn": "postgresql://user:secretpw@host:5432/db"}}
    out = redact_config(cfg)
    masked = out["memory"]["pg_dsn"]
    assert "secretpw" not in masked
    assert "user" in masked and "host" in masked and "5432" in masked


def test_does_not_mutate_original():
    cfg = {"k": {"api_key": "sk-longvalue123"}}
    orig = cfg["k"]["api_key"]
    redact_config(cfg)
    assert cfg["k"]["api_key"] == orig  # 原 cfg 不变


def test_non_sensitive_keys_untouched():
    cfg = {"data_dir": "/x", "port": 18083, "list": [1, 2, 3], "nested": {"name": "ok"}}
    assert redact_config(cfg) == cfg


def test_deep_nested_masking():
    cfg = {"a": {"b": {"c": [{"secret": "deepsecretval", "ok": 1}]}}}
    out = redact_config(cfg)
    assert out["a"]["b"]["c"][0]["secret"] == "deep***"
    assert out["a"]["b"]["c"][0]["ok"] == 1


def test_short_value_fully_masked():
    cfg = {"token": "abc"}
    assert redact_config(cfg)["token"] == "***"


def test_empty_value_preserved():
    cfg = {"api_key": "", "password": None}
    out = redact_config(cfg)
    assert out["api_key"] == "" and out["password"] is None
