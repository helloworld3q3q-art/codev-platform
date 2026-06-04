"""M-P3 多 dev 护栏 —— multi_user_policy_error 纯函数 + memory list CLI 接线。"""
from __future__ import annotations

from codev_platform.gateway.auth import multi_user_policy_error


# ---- multi_user_policy_error(纯函数:多 dev + passthrough → 拒绝)----

def test_token_mode_always_ok():
    # 已是 token 模式 → 永远放行(即便多 token)
    cfg = {"gateway": {"auth_mode": "token", "multi_user": True,
                       "tokens": {"h1": {}, "h2": {}}}}
    assert multi_user_policy_error(cfg) is None


def test_single_user_passthrough_ok():
    # 单人 passthrough(0/1 token, 无 multi_user 标)→ 放行(本机自己用, 无人可串)
    assert multi_user_policy_error({"gateway": {"auth_mode": "passthrough"}}) is None
    assert multi_user_policy_error(
        {"gateway": {"auth_mode": "passthrough", "tokens": {"h1": {}}}}) is None
    assert multi_user_policy_error(None) is None


def test_multi_user_flag_passthrough_rejected():
    err = multi_user_policy_error({"gateway": {"auth_mode": "passthrough", "multi_user": True}})
    assert err and "multi_user" in err and "token" in err


def test_more_than_one_token_passthrough_rejected():
    err = multi_user_policy_error(
        {"gateway": {"auth_mode": "passthrough", "tokens": {"h1": {}, "h2": {}}}})
    assert err and "2" in err and "token" in err


# ---- memory list CLI 接线 ----

def test_cli_registers_memory_list():
    from codev_platform.cli import build_parser
    parser = build_parser()
    ns = parser.parse_args(["memory", "list", "--scope", "project",
                            "--scope-ref", "proj1", "--limit", "5"])
    assert ns.action == "list" and ns.scope == "project"
    assert ns.scope_ref == "proj1" and ns.limit == 5 and callable(ns.func)


def test_cli_memory_list_defaults_personal():
    from codev_platform.cli import build_parser
    ns = build_parser().parse_args(["memory", "list"])
    assert ns.action == "list" and ns.scope == "personal"


# ---- memory_mcp 启动期拒绝(多 dev + passthrough)----

def test_memory_mcp_run_http_refuses_multi_user(monkeypatch):
    import asyncio

    import pytest

    import codev_platform.core.config as cfgmod
    from codev_platform.agent import memory_mcp
    monkeypatch.setattr(cfgmod, "load_config",
                        lambda: {"gateway": {"auth_mode": "passthrough", "multi_user": True}})
    with pytest.raises(SystemExit):
        asyncio.run(memory_mcp.run_http(port=0))  # 策略闸在绑端口前 fail-fast
