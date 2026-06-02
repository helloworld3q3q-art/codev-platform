"""Projects 垂直片测试 (plan §十五 Projects) —— list/register/detail/load/unload。

覆盖: list 返回 PageResult 结构 + register 幂等 (重复 code 报统一错误 envelope) +
detail 未知项目 PROJECT_UNKNOWN + load/unload 运行态翻转。
本 venv 未装 fastapi → importorskip 自动 skip。

数据源隔离: 用临时 meta 目录 (注入 read/write repo), 不碰仓内真实 platform_meta。
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from codev_platform.core.httpkit import build_app  # noqa: E402
from codev_platform.web.repositories import project_write_repo  # noqa: E402
from codev_platform.web.repositories.project_read_repo import ProjectReadRepository  # noqa: E402
from codev_platform.web.repositories.project_write_repo import ProjectWriteRepository  # noqa: E402
from codev_platform.web.routes import projects  # noqa: E402
from codev_platform.web.services.project_service import ProjectService  # noqa: E402

_CFG = {"gateway": {"auth_mode": "passthrough"}, "projects": {}}


def _seed(meta_dir, code, name="Demo"):
    target = meta_dir / code / "meta.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"project_id": code, "display_name": name}), encoding="utf-8")


@pytest.fixture
def client(tmp_path):
    meta_dir = tmp_path / "projects"
    meta_dir.mkdir()
    _seed(meta_dir, "alpha", "Alpha")
    _seed(meta_dir, "beta", "Beta")
    project_write_repo._LOADED.clear()  # 进程内运行态隔离

    def _svc() -> ProjectService:
        return ProjectService(
            read_repo=ProjectReadRepository(meta_dir=meta_dir),
            write_repo=ProjectWriteRepository(meta_dir=meta_dir),
        )

    app = build_app(title="t", routers=[projects.router], cfg=_CFG)
    app.dependency_overrides[projects._service] = _svc
    return TestClient(app), meta_dir


def test_list_returns_page_result_structure(client):
    c, _ = client
    r = c.post("/api/v1/projects/list")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["pageNumber"] == 1 and body["pageSize"] == 20
    assert body["total"] == 2
    codes = {it["code"] for it in body["data"]}
    assert codes == {"alpha", "beta"}
    assert body["requestId"]


def test_register_then_detail(client):
    c, _ = client
    r = c.post("/api/v1/projects/register",
               json={"code": "gamma", "name": "Gamma", "repoPath": "/x"})
    assert r.status_code == 200
    assert r.json()["data"]["code"] == "gamma"
    d = c.get("/api/v1/projects/detail", params={"code": "gamma"})
    assert d.status_code == 200
    assert d.json()["data"]["name"] == "Gamma"


def test_register_idempotent_duplicate_is_unified_error(client):
    c, _ = client
    payload = {"code": "delta", "name": "Delta"}
    assert c.post("/api/v1/projects/register", json=payload).status_code == 200
    r = c.post("/api/v1/projects/register", json=payload)
    assert r.status_code == 400
    body = r.json()
    assert body["success"] is False
    assert body["code"] == "invalid_params"
    assert body["requestId"]


def test_detail_unknown_project_is_project_unknown(client):
    c, _ = client
    r = c.get("/api/v1/projects/detail", params={"code": "nope"})
    assert r.status_code == 404
    body = r.json()
    assert body["success"] is False
    assert body["code"] == "project_unknown"


def test_load_then_unload_toggles_runtime_state(client):
    c, _ = client
    lr = c.post("/api/v1/projects/load", json={"code": "alpha"})
    assert lr.status_code == 200
    assert lr.json()["data"]["loaded"] is True
    # detail 反映运行态
    d = c.get("/api/v1/projects/detail", params={"code": "alpha"})
    assert d.json()["data"]["loaded"] is True
    ur = c.post("/api/v1/projects/unload", json={"code": "alpha"})
    assert ur.json()["data"]["loaded"] is False


def test_load_unknown_project_is_project_unknown(client):
    c, _ = client
    r = c.post("/api/v1/projects/load", json={"code": "ghost"})
    assert r.status_code == 404
    assert r.json()["code"] == "project_unknown"
