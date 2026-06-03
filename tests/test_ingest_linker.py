"""核心 cross-plugin linker pass 测试 (unified-graph-lineage P3)。

验证 graph.ingest.ingest_project 末尾的 linker pass:
- 跨所有后端插件 (fastapi + spring) 把 frontend_api_call --calls_api--> backend_endpoint 连起来
- calls_api 单一 owner = builtin.linker (前端插件不再各自产)
- 前端能链到 Java/Spring 端点 (旧 per-plugin 实现链不到的缺口)
- 重跑 ingest 幂等 (calls_api 数不翻倍)
"""
from __future__ import annotations

from pathlib import Path

from codev_platform.graph.ingest import LINKER_PLUGIN, ingest_project
from codev_platform.graph.schema import EdgeKind
from codev_platform.graph.store import load_graph, open_store

PID = "demo-linker"

# 前端: 调 /api/v1/foo (FastAPI) 和 /api/v1/bar (Spring) 两个后端。
_FE_API = """\
import { post } from '@/utils/fetch';
export async function postFoo(data) { return post({ url: '/api/v1/foo', data }); }
export async function postBar(data) { return post({ url: '/api/v1/bar', data }); }
"""
_FASTAPI = '''\
from fastapi import APIRouter
router = APIRouter()
@router.post("/api/v1/foo")
def foo():
    return 1
'''
_SPRING = """\
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1")
public class BarController {
    @PostMapping("/bar")
    public Object bar() { return null; }
}
"""


def _build(repo: Path) -> None:
    (repo / "package.json").write_text('{"dependencies":{"react":"18"}}', encoding="utf-8")
    api = repo / "src" / "services" / "apis"
    api.mkdir(parents=True)
    (api / "fooapi.ts").write_text(_FE_API, encoding="utf-8")
    (repo / "routes.py").write_text(_FASTAPI, encoding="utf-8")
    (repo / "BarController.java").write_text(_SPRING, encoding="utf-8")


def test_linker_links_frontend_to_fastapi_and_spring(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build(repo)
    store = tmp_path / "store.sqlite"

    report = ingest_project(repo, PID, store_path=store)
    assert LINKER_PLUGIN in report.ingested

    conn = open_store(PID, path=store)
    try:
        graph = load_graph(conn, PID)
    finally:
        conn.close()

    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS_API.value]
    targets = {e.target for e in calls}
    # 前端 -> FastAPI(py) 和 -> Spring(java) 两条都连上 (跨插件)。
    assert f"{PID}:backend_endpoint:POST:/api/v1/foo" in targets
    assert f"{PID}:backend_endpoint:POST:/api/v1/bar" in targets
    assert len(calls) == 2


def test_linker_idempotent_on_reingest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build(repo)
    store = tmp_path / "store.sqlite"

    ingest_project(repo, PID, store_path=store)
    ingest_project(repo, PID, store_path=store)  # 重跑

    conn = open_store(PID, path=store)
    try:
        graph = load_graph(conn, PID)
    finally:
        conn.close()
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS_API.value]
    assert len(calls) == 2  # 不翻倍
