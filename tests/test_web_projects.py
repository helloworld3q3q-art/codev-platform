"""Projects 垂直片测试 (plan §十五 Projects) —— list/register/detail/load/unload + 鉴权。

覆盖:
- 功能性 (org admin 基线): list 结构 / register 幂等 / detail 未知 / load-unload 运行态翻转。
- 鉴权 (用户要求: org 隔离 + 逐项目权限):
  * 未登录 → 403 (deep-audit-2026-06-03 P1: 原零鉴权回归);
  * 普通成员只见自己有 project_role 的项目 (逐项目可见性);
  * 跨 org 访问 → 403 (org 隔离);
  * 非 org admin 不能 register;
  * 成员 write 角色可 load 有授予的项目, 无授予 → 403。

鉴权走**真实** session_store + current_session (不 override 授权依赖), 故覆盖完整认证链。
数据源隔离: 用临时 meta 目录 (注入 read/write repo), 不碰仓内真实 platform_meta。
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from codev_platform.core.httpkit import build_app  # noqa: E402
from codev_platform.web.domain.accounts import OrgMember  # noqa: E402
from codev_platform.web.repositories import project_write_repo  # noqa: E402
from codev_platform.web.repositories.account_store import (  # noqa: E402
    get_member_store,
    reset_account_stores,
)
from codev_platform.web.repositories.project_read_repo import ProjectReadRepository  # noqa: E402
from codev_platform.web.repositories.project_write_repo import ProjectWriteRepository  # noqa: E402
from codev_platform.web.routes import projects  # noqa: E402
from codev_platform.web.security.sessions import session_store  # noqa: E402
from codev_platform.web.services.project_service import ProjectService  # noqa: E402

_CFG = {"gateway": {"auth_mode": "passthrough"}, "projects": {}}


def _seed(meta_dir, code, name="Demo", org_id=None):
    target = meta_dir / code / "meta.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    meta = {"project_id": code, "display_name": name}
    if org_id:
        meta["org_id"] = org_id
    target.write_text(json.dumps(meta), encoding="utf-8")


def _login(username: str, org_id: str) -> dict:
    """建真实会话, 返回 Authorization 头 (走真实 current_session 解析路径)。"""
    issued = session_store.create(username, org_id)
    return {"Authorization": f"Bearer {issued.access_token}"}


def _member(org_id: str, username: str, role: str, project_roles: dict | None = None) -> None:
    get_member_store().upsert(
        OrgMember(org_id=org_id, username=username, role=role,
                  project_roles=project_roles or {})
    )


def _app(meta_dir) -> TestClient:
    def _svc() -> ProjectService:
        return ProjectService(
            read_repo=ProjectReadRepository(meta_dir=meta_dir),
            write_repo=ProjectWriteRepository(meta_dir=meta_dir),
        )
    app = build_app(title="t", routers=[projects.router], cfg=_CFG)
    app.dependency_overrides[projects._service] = _svc
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_state():
    project_write_repo._LOADED.clear()
    session_store.clear()
    reset_account_stores()
    yield
    project_write_repo._LOADED.clear()
    session_store.clear()
    reset_account_stores()


@pytest.fixture
def admin_client(tmp_path):
    """org acme 的 admin, seed 两个公开项目 (alpha/beta) —— 功能性基线。"""
    meta_dir = tmp_path / "projects"
    meta_dir.mkdir()
    _seed(meta_dir, "alpha", "Alpha")
    _seed(meta_dir, "beta", "Beta")
    _member("acme", "adm", "admin")
    return _app(meta_dir), meta_dir, _login("adm", "acme")


# ----- 功能性 (org admin 基线) -----

def test_list_returns_page_result_structure(admin_client):
    c, _, h = admin_client
    r = c.post("/api/v1/projects/list", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["result"] == 0
    assert body["currentPage"] == 1 and body["pageSize"] == 20
    assert body["total"] == 2 and body["totalPage"] == 1
    assert {it["code"] for it in body["data"]} == {"alpha", "beta"}
    assert body["requestId"]


def test_register_then_detail(admin_client):
    c, _, h = admin_client
    r = c.post("/api/v1/projects/register",
               json={"code": "gamma", "name": "Gamma", "repoPath": "/x"}, headers=h)
    assert r.status_code == 200
    assert r.json()["data"]["code"] == "gamma"
    d = c.get("/api/v1/projects/detail", params={"code": "gamma"}, headers=h)
    assert d.status_code == 200
    assert d.json()["data"]["name"] == "Gamma"


def test_register_idempotent_duplicate_is_unified_error(admin_client):
    c, _, h = admin_client
    payload = {"code": "delta", "name": "Delta"}
    assert c.post("/api/v1/projects/register", json=payload, headers=h).status_code == 200
    r = c.post("/api/v1/projects/register", json=payload, headers=h)
    assert r.status_code == 400
    body = r.json()
    assert body["result"] == 1
    assert body["errors"][0]["errorCode"] == "invalid_params"


def test_detail_unknown_project_is_project_unknown(admin_client):
    c, _, h = admin_client
    r = c.get("/api/v1/projects/detail", params={"code": "nope"}, headers=h)
    assert r.status_code == 404
    assert r.json()["errors"][0]["errorCode"] == "project_unknown"


def test_load_then_unload_toggles_runtime_state(admin_client):
    c, _, h = admin_client
    lr = c.post("/api/v1/projects/load", json={"code": "alpha"}, headers=h)
    assert lr.status_code == 200 and lr.json()["data"]["loaded"] is True
    d = c.get("/api/v1/projects/detail", params={"code": "alpha"}, headers=h)
    assert d.json()["data"]["loaded"] is True
    ur = c.post("/api/v1/projects/unload", json={"code": "alpha"}, headers=h)
    assert ur.json()["data"]["loaded"] is False


def test_load_unknown_project_is_project_unknown(admin_client):
    c, _, h = admin_client
    r = c.post("/api/v1/projects/load", json={"code": "ghost"}, headers=h)
    assert r.status_code == 404
    assert r.json()["errors"][0]["errorCode"] == "project_unknown"


def test_register_lands_in_caller_org(admin_client):
    c, meta_dir, h = admin_client
    # 非 platform_admin 的 org admin: 即便传 orgId=other, 也落到自己 org (跨 org 建项目被收紧)。
    r = c.post("/api/v1/projects/register",
               json={"code": "orgproj", "name": "OrgProj", "orgId": "other"}, headers=h)
    assert r.status_code == 200
    meta = json.loads((meta_dir / "orgproj" / "meta.json").read_text(encoding="utf-8"))
    assert meta["org_id"] == "acme"


def test_list_filter_by_keyword(admin_client):
    c, _, h = admin_client
    r = c.post("/api/v1/projects/list", json={"keyword": "alph"}, headers=h)
    assert {it["code"] for it in r.json()["data"]} == {"alpha"}
    r2 = c.post("/api/v1/projects/list", json={"keyword": "BETA"}, headers=h)
    assert {it["code"] for it in r2.json()["data"]} == {"beta"}


# ----- 鉴权 -----

def test_unauthenticated_list_is_denied(tmp_path):
    """deep-audit P1 回归: 未登录不得访问项目列表。"""
    meta_dir = tmp_path / "projects"
    meta_dir.mkdir()
    _seed(meta_dir, "alpha", "Alpha")
    c = _app(meta_dir)
    r = c.post("/api/v1/projects/list")  # 无 Authorization
    assert r.status_code == 403
    assert r.json()["errors"][0]["errorCode"] == "access_denied"


def test_member_sees_only_granted_projects(tmp_path):
    """逐项目可见性: 普通成员仅见自己有 project_role 的项目。"""
    meta_dir = tmp_path / "projects"
    meta_dir.mkdir()
    _seed(meta_dir, "alpha", "Alpha")
    _seed(meta_dir, "beta", "Beta")
    _member("acme", "dev", "member", {"alpha": "member"})
    c = _app(meta_dir)
    r = c.post("/api/v1/projects/list", headers=_login("dev", "acme"))
    assert r.status_code == 200
    assert {it["code"] for it in r.json()["data"]} == {"alpha"}


def test_member_load_requires_granted_write(tmp_path):
    """成员 write 角色可 load 有授予项目; 无授予 → 403。"""
    meta_dir = tmp_path / "projects"
    meta_dir.mkdir()
    _seed(meta_dir, "alpha", "Alpha")
    _seed(meta_dir, "beta", "Beta")
    _member("acme", "dev", "member", {"alpha": "member"})
    c = _app(meta_dir)
    h = _login("dev", "acme")
    assert c.post("/api/v1/projects/load", json={"code": "alpha"}, headers=h).status_code == 200
    denied = c.post("/api/v1/projects/load", json={"code": "beta"}, headers=h)
    assert denied.status_code == 403


def test_viewer_cannot_load(tmp_path):
    """viewer 角色 (rank 1) 不覆盖 write → load 403。"""
    meta_dir = tmp_path / "projects"
    meta_dir.mkdir()
    _seed(meta_dir, "alpha", "Alpha")
    _member("acme", "ro", "viewer", {"alpha": "viewer"})
    c = _app(meta_dir)
    h = _login("ro", "acme")
    # viewer 可 read (detail), 不可 write (load)
    assert c.get("/api/v1/projects/detail", params={"code": "alpha"}, headers=h).status_code == 200
    assert c.post("/api/v1/projects/load", json={"code": "alpha"}, headers=h).status_code == 403


def test_cross_org_detail_denied(tmp_path):
    """org 隔离: 成员不能访问别组织的项目。"""
    meta_dir = tmp_path / "projects"
    meta_dir.mkdir()
    _seed(meta_dir, "betaproj", "BetaProj", org_id="beta")  # 属 org beta
    _member("acme", "dev", "admin", {"betaproj": "admin"})  # acme 用户即便有 grant
    c = _app(meta_dir)
    r = c.get("/api/v1/projects/detail", params={"code": "betaproj"}, headers=_login("dev", "acme"))
    assert r.status_code == 403


def test_non_admin_cannot_register(tmp_path):
    """register 须 org admin: 普通成员 → 403。"""
    meta_dir = tmp_path / "projects"
    meta_dir.mkdir()
    _member("acme", "dev", "member")
    c = _app(meta_dir)
    r = c.post("/api/v1/projects/register",
               json={"code": "x", "name": "X"}, headers=_login("dev", "acme"))
    assert r.status_code == 403
