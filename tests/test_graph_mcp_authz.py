"""graph MCP/SSE 边界鉴权 wiring(authorize_graph_request) —— 锁多组织 org 隔离。

graph 有两个网络入口: HTTP(test_web_graph_authz 守护)与 **MCP SSE**(本文件)。MCP 边界的
org/项目隔离闸 = handle_sse 与 bind_mcp_context **共用**的 authorize_graph_request → core.acl.
can_access。本测锁: 跨 org(org-A 身份查 org-B 项目)→ 403; 同 org 放行; 无身份(token 模式)→ 403;
非法 project_id → 400。can_access 本身的隔离逻辑由 test_acl 覆盖, 这里锁"graph 边界确实调它且
拒绝映射到 403"的 wiring(防有人把闸从 MCP 入口摘掉)。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")
pytest.importorskip("starlette")

from codev_platform.graph import mcp_server  # noqa: E402


def _idn(org_id: str):
    return SimpleNamespace(via="token", org_id=org_id, projects=frozenset(),
                           all_projects=True, user_id="u")


@pytest.fixture(autouse=True)
def _token_cfg_crossorg(monkeypatch):
    # token 模式 + openclaw-stock 属 org-B(镜像 test_acl 跨 org 搭法); audit 免 IO。
    cfg = {"gateway": {"auth_mode": "token"}, "projects": {"openclaw-stock": {"org_id": "orgB"}}}
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: cfg)
    monkeypatch.setattr("codev_platform.core.audit.audit_access", lambda *a, **k: None)


def test_cross_org_denied_403():
    # org-A 身份查 org-B 的项目 → MCP 边界 403(不进 SSE、不返图谱)。
    pid, denial = mcp_server.authorize_graph_request("openclaw-stock", _idn("orgA"))
    assert pid is None
    assert denial is not None and denial.status_code == 403


def test_same_org_allowed():
    pid, denial = mcp_server.authorize_graph_request("openclaw-stock", _idn("orgB"))
    assert denial is None and pid == "openclaw-stock"


def test_no_identity_denied_in_token_mode():
    pid, denial = mcp_server.authorize_graph_request("openclaw-stock", None)
    assert pid is None and denial is not None and denial.status_code == 403


def test_invalid_project_id_400():
    pid, denial = mcp_server.authorize_graph_request("../escape", _idn("orgB"))
    assert pid is None and denial is not None and denial.status_code == 400
