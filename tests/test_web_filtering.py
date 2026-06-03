"""org/project 过滤测试 —— jobs list 按当前项目过滤 + projects list 按当前 org 过滤。"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from codev_platform.core.httpkit import build_app  # noqa: E402
from codev_platform.web.services.job_service import build_in_memory_job_service  # noqa: E402

_CFG = {"gateway": {"auth_mode": "passthrough"}, "projects": {}}


# ---- jobs list 按 project 过滤 ----

def test_jobs_list_filters_by_project():
    svc = build_in_memory_job_service()
    svc.submit("projA", "index_rebuild:chroma")
    svc.submit("projA", "index_rebuild:codegraph")  # 同 project 不同 type, 不撞锁
    svc.submit("projB", "index_rebuild:chroma")

    a, ta = svc.list_jobs(project_id="projA", offset=0, limit=50)
    assert ta == 2 and all(j.project_id == "projA" for j in a)
    _b, tb = svc.list_jobs(project_id="projB", offset=0, limit=50)
    assert tb == 1
    _all, tall = svc.list_jobs(project_id=None, offset=0, limit=50)
    assert tall == 3


def test_jobs_list_route_returns_pageresult(monkeypatch):
    from codev_platform.web.routes import jobs as jr

    svc = build_in_memory_job_service()
    svc.submit("demo", "index_rebuild:chroma")
    monkeypatch.setattr(jr, "job_service", svc)  # 注入隔离的 service (避开模块单例污染)
    app = build_app(title="t", routers=[jr.router], cfg=_CFG, public_paths=("/health",))
    r = TestClient(app).post("/api/v1/jobs/list", json={}, headers={"X-Project-Id": "demo"})
    assert r.status_code == 200
    body = r.json()
    assert body["result"] == 0 and body["currentPage"] == 1 and body["total"] == 1
    assert body["data"][0]["projectId"] == "demo"


# ---- projects list 按 org 过滤 ----

class _FakeProjectRead:
    def list_projects(self):
        return [
            {"code": "p1", "name": "P1", "status": "ACTIVE"},
            {"code": "p2", "name": "P2", "status": "ACTIVE"},
            {"code": "pub", "name": "Pub", "status": "ACTIVE"},
        ]


def test_projects_list_filters_by_org(monkeypatch):
    """org 隔离 (新模型): org admin 看本 org + 公开项目; platform admin 看全部。"""
    from codev_platform.web.security import membership as mem
    from codev_platform.web.services import project_service as ps
    from codev_platform.web.domain.accounts import OrgMember
    from codev_platform.web.repositories.account_store import (
        get_member_store,
        reset_account_stores,
    )
    from codev_platform.web.security.sessions import Session
    from codev_platform.web.services.project_service import ProjectService

    # p1→orgA, p2→orgB, pub 无 org_id = 公开 (本 org admin 可见)。
    cfg = {"projects": {"p1": {"org_id": "orgA"}, "p2": {"org_id": "orgB"}}}
    monkeypatch.setattr(ps, "load_config", lambda: cfg)
    monkeypatch.setattr(mem, "load_config", lambda: cfg)
    # platform admin 仅 "super"; 其余按 org 角色判。
    monkeypatch.setattr(ps, "is_platform_admin", lambda c, u: u == "super")
    monkeypatch.setattr(mem, "is_platform_admin", lambda c, u: u == "super")

    reset_account_stores()
    get_member_store().upsert(OrgMember(org_id="orgA", username="a", role="admin"))
    get_member_store().upsert(OrgMember(org_id="orgB", username="b", role="admin"))
    svc = ProjectService(read_repo=_FakeProjectRead())

    def _sess(org, user):
        return Session(session_id="t", username=user, org_id=org,
                       access_expires_at=9e18, refresh_expires_at=9e18)

    try:
        a, _ = svc.list_projects(sess=_sess("orgA", "a"), offset=0, limit=50)
        assert {i.code for i in a} == {"p1", "pub"}
        b, _ = svc.list_projects(sess=_sess("orgB", "b"), offset=0, limit=50)
        assert {i.code for i in b} == {"p2", "pub"}
        allp, tall = svc.list_projects(sess=_sess("any", "super"), offset=0, limit=50)
        assert tall == 3 and {i.code for i in allp} == {"p1", "p2", "pub"}
    finally:
        reset_account_stores()
