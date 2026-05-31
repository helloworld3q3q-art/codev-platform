"""RBAC 接线测试(M5):deps.get_rbac_store 回退 + LocalRecallService 双路 + memory 路由语法。

环境:平台 venv 无 psycopg/PG。所以这里只测**接线逻辑**(回退 None / 注入 fake store),
不连真库;PG 实表验证留给 scripts/verify_rbac_pg.py。
"""
from __future__ import annotations

import py_compile
from pathlib import Path

from codev_platform.agent.recall_service import LocalRecallService, visible_scopes
from codev_platform.core.rbac import Membership


# ---- fakes(不连 PG)----

class _FakeMemoryStore:
    """list_scope 记录被查作用域,返回空(召回逻辑由其它测试覆盖,这里只验作用域来源)。"""

    def __init__(self) -> None:
        self.queried: list[tuple[str, str]] = []

    def list_scope(self, scope, ref, *, org_id, limit):  # noqa: ANN001
        self.queried.append((scope, ref))
        return []


class _FakeRbacStore:
    """fetch_membership 返回注入的 Membership,记录入参。"""

    def __init__(self, membership: Membership) -> None:
        self._m = membership
        self.calls: list[tuple] = []

    def fetch_membership(self, org_id, user_id, project_id=None):  # noqa: ANN001
        self.calls.append((org_id, user_id, project_id))
        return self._m


# ---- deps.get_rbac_store 无 dsn → None ----

def test_get_rbac_store_none_without_dsn(monkeypatch):
    from codev_platform.agent import deps

    # 重置单例 + 强制 DSN 解析为空
    monkeypatch.setattr(deps, "_rbac_store_built", False, raising=False)
    monkeypatch.setattr(deps, "_rbac_store", None, raising=False)
    monkeypatch.setattr(deps.acfg, "env_or_config", lambda *a, **k: None)
    assert deps.get_rbac_store() is None


# ---- recall_service 无 rbac_store → 用模块桩(现有行为不变)----

def test_recall_visible_scopes_falls_back_to_stub():
    store = _FakeMemoryStore()
    svc = LocalRecallService(store)  # rbac_store=None
    scopes = svc._visible_scopes("acme", "alice", "proj1")
    assert scopes == visible_scopes("acme", "alice", "proj1")
    assert ("org", "org") in scopes
    assert ("project", "proj1") in scopes
    assert ("personal", "alice") in scopes


# ---- recall_service 有 rbac_store → 走 compute_visible_scopes(真实 Membership)----

def test_recall_visible_scopes_uses_rbac_store():
    mem_store = _FakeMemoryStore()
    # viewer 仅 org+personal,无 project_role(不应出现 project 作用域)
    m = Membership(org_role="viewer", teams=(("teamA", "member"),), project_role=None)
    rbac = _FakeRbacStore(m)
    svc = LocalRecallService(mem_store, rbac_store=rbac)
    scopes = svc._visible_scopes("acme", "alice", "proj1")

    assert rbac.calls == [("acme", "alice", "proj1")]
    assert ("org", "org") in scopes
    assert ("team", "teamA") in scopes
    assert ("personal", "alice") in scopes
    # project_role=None → 不可见 project 作用域(权限收敛)
    assert ("project", "proj1") not in scopes


def test_recall_visible_scopes_no_user_falls_back():
    """user_id 为空 → 即便有 rbac_store 也回退桩(无身份不查 Membership)。"""
    mem_store = _FakeMemoryStore()
    rbac = _FakeRbacStore(Membership())
    svc = LocalRecallService(mem_store, rbac_store=rbac)
    scopes = svc._visible_scopes("acme", None, "proj1")
    assert rbac.calls == []
    assert scopes == visible_scopes("acme", None, "proj1")


# ---- memory 路由语法(无 fastapi 时 py_compile 验)----

def test_memory_route_py_compiles():
    p = (
        Path(__file__).resolve().parents[1]
        / "codev_platform" / "agent" / "routes" / "memory.py"
    )
    py_compile.compile(str(p), doraise=True)
