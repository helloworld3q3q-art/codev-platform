"""Web Jobs + Indexes 垂直片测试 (plan §十二 / §十五)。

覆盖: 提交 rebuild 返回 jobId + 同 project 同类型再次提交互斥 (RATE_LIMITED) +
job detail 查询 + cancel 改状态 + 未知 job 报错 envelope。本 venv 未装 fastapi → skip。
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from codev_platform.core.httpkit import build_app  # noqa: E402
from codev_platform.web.domain.locks import registry as lock_registry  # noqa: E402
from codev_platform.web.repositories.job_write_repo import new_in_memory_store  # noqa: E402
from codev_platform.web.routes import indexes, jobs  # noqa: E402
from codev_platform.web.repositories.job_read_repo import InMemoryJobReadRepo  # noqa: E402
from codev_platform.web.repositories.job_write_repo import InMemoryJobWriteRepo  # noqa: E402
from codev_platform.web.services.index_service import IndexService  # noqa: E402
from codev_platform.web.services.job_service import JobService  # noqa: E402

_CFG = {"gateway": {"auth_mode": "passthrough"}, "projects": {}}
_HEADERS = {"X-Project-Id": "demo-proj"}


@pytest.fixture(autouse=True)
def _fresh_state():
    """每个用例独立 store + 干净项目锁 (模块级单例需手动复位)。"""
    store = new_in_memory_store()
    svc = JobService(
        read_repo=InMemoryJobReadRepo(store),
        write_repo=InMemoryJobWriteRepo(store),
        locks=lock_registry,
    )
    jobs.job_service = svc
    indexes.index_service = IndexService(svc)
    # 清空任何残留锁
    lock_registry._held.clear()  # noqa: SLF001  (测试复位, 非生产路径)
    yield
    lock_registry._held.clear()  # noqa: SLF001


def _client() -> TestClient:
    return TestClient(build_app(title="t", routers=[jobs.router, indexes.router], cfg=_CFG))


def test_rebuild_returns_job_id():
    c = _client()
    r = c.post("/api/v1/indexes/rebuild", json={"indexKind": "chroma"}, headers=_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["result"] == 0
    assert body["data"]["jobId"]
    assert body["requestId"]


def test_rebuild_default_kind_all():
    c = _client()
    r = c.post("/api/v1/indexes/rebuild", json={}, headers=_HEADERS)
    assert r.status_code == 200
    assert r.json()["data"]["jobId"]


def test_rebuild_unknown_kind_is_invalid_params():
    c = _client()
    r = c.post("/api/v1/indexes/rebuild", json={"indexKind": "nope"}, headers=_HEADERS)
    assert r.status_code == 400
    assert r.json()["errors"][0]["errorCode"] == "invalid_params"


def test_rebuild_ingest_kind_allowed():
    # P2 修复(codex #6): ingest 是注册的 runner kind, API 应能单独请求(原硬编码 allowed 漏了,
    # 只能走 all 触发全量重建 + 锁冲突)。_allowed_kinds 改动态读 runners.kinds() 后对齐。
    c = _client()
    r = c.post("/api/v1/indexes/rebuild", json={"indexKind": "ingest"}, headers=_HEADERS)
    assert r.status_code == 200
    assert r.json()["data"]["jobId"]


def test_resubmit_same_kind_not_permanently_blocked():
    # 审计事实A 修复: 锁不跨 submit 悬挂 —— 同 project 同 kind 连续提交各返 jobId(不再像修复前
    # 第二次永久 RATE_LIMITED)。提交完成即释放锁; 不重复跑由下游 FileSpoolQueue 同 key 合并保证(非本锁)。
    c = _client()
    r1 = c.post("/api/v1/indexes/rebuild", json={"indexKind": "chroma"}, headers=_HEADERS)
    r2 = c.post("/api/v1/indexes/rebuild", json={"indexKind": "chroma"}, headers=_HEADERS)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["data"]["jobId"] and r2.json()["data"]["jobId"]


def test_different_kind_not_blocked():
    c = _client()
    r1 = c.post("/api/v1/indexes/rebuild", json={"indexKind": "chroma"}, headers=_HEADERS)
    r2 = c.post("/api/v1/indexes/rebuild", json={"indexKind": "codegraph"}, headers=_HEADERS)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["data"]["jobId"] != r2.json()["data"]["jobId"]


def test_job_detail_query():
    c = _client()
    job_id = c.post(
        "/api/v1/indexes/rebuild", json={"indexKind": "chroma"}, headers=_HEADERS
    ).json()["data"]["jobId"]
    r = c.get("/api/v1/jobs/detail", params={"jobId": job_id}, headers=_HEADERS)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["jobId"] == job_id
    assert data["projectId"] == "demo-proj"
    assert data["jobType"] == "index_rebuild:chroma"
    assert data["status"] == "Pending"


def test_cancel_changes_status_and_releases_lock():
    c = _client()
    job_id = c.post(
        "/api/v1/indexes/rebuild", json={"indexKind": "chroma"}, headers=_HEADERS
    ).json()["data"]["jobId"]
    r = c.post("/api/v1/jobs/cancel", json={"jobId": job_id}, headers=_HEADERS)
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "Cancelled"
    # 锁释放后, 同 project 同 kind 可再次提交
    r2 = c.post("/api/v1/indexes/rebuild", json={"indexKind": "chroma"}, headers=_HEADERS)
    assert r2.status_code == 200


def test_cancel_terminal_job_rejected():
    c = _client()
    job_id = c.post(
        "/api/v1/indexes/rebuild", json={"indexKind": "chroma"}, headers=_HEADERS
    ).json()["data"]["jobId"]
    c.post("/api/v1/jobs/cancel", json={"jobId": job_id}, headers=_HEADERS)
    # 再取消已 Cancelled 的 job → invalid_params
    r = c.post("/api/v1/jobs/cancel", json={"jobId": job_id}, headers=_HEADERS)
    assert r.status_code == 400
    assert r.json()["errors"][0]["errorCode"] == "invalid_params"


def test_unknown_job_is_error_envelope():
    c = _client()
    r = c.get("/api/v1/jobs/detail", params={"jobId": "nope-xyz"}, headers=_HEADERS)
    assert r.status_code == 404
    body = r.json()
    assert body["result"] == 1
    assert body["errors"][0]["errorCode"] == "project_unknown"
    assert body["requestId"]


def test_job_cross_project_detail_cancel_is_not_found():
    # P1 修复(codex bug-edge-audit): 对别项目有权(passthrough)+ 知道本项目 jobId, 不能跨项目
    # detail/cancel。跨项目统一当 not found(不泄露 job 存在但非本项目)。
    c = _client()
    job_id = c.post(
        "/api/v1/indexes/rebuild", json={"indexKind": "chroma"}, headers=_HEADERS
    ).json()["data"]["jobId"]
    other = {"X-Project-Id": "other-proj"}
    # 别项目 header 查同 jobId → 404 not found
    r = c.get("/api/v1/jobs/detail", params={"jobId": job_id}, headers=other)
    assert r.status_code == 404 and r.json()["errors"][0]["errorCode"] == "project_unknown"
    # 别项目 header 取消同 jobId → 404(不能跨项目取消)
    rc = c.post("/api/v1/jobs/cancel", json={"jobId": job_id}, headers=other)
    assert rc.status_code == 404 and rc.json()["errors"][0]["errorCode"] == "project_unknown"
    # 本项目仍能正常 detail
    assert c.get("/api/v1/jobs/detail", params={"jobId": job_id}, headers=_HEADERS).status_code == 200
