"""ingest 端到端测试 —— 注册假插件 -> ingest_project -> store 有数据。

用临时 store 路径 + 临时注册表, 不碰真实 data/ 与真实插件。验证:
- 适用插件产出落进 store (端到端)
- 单插件崩溃被隔离, 不影响其余入库
- project 隔离 (不同 pid 不串)
- 重跑 ingest 幂等 (不翻倍)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from codev_platform.graph.ingest import ingest_project
from codev_platform.graph.schema import AnalyzerResult, GraphEdge, GraphNode
from codev_platform.graph.store import load_graph, open_store
from codev_platform.plugins import clear_registry, register_plugin
from codev_platform.plugins.base import AnalyzerPlugin


class _OkPlugin(AnalyzerPlugin):
    name = "fake.ok"
    version = "1.0.0"

    def detect(self, repo_path: Path) -> bool:
        return True

    def analyze(self, repo_path: Path, project_id: str) -> AnalyzerResult:
        return AnalyzerResult(
            nodes=[GraphNode(id=f"{project_id}:project:root", kind="project",
                             name="root", project_id=project_id)],
            edges=[GraphEdge(source=f"{project_id}:project:root",
                             target=f"{project_id}:project:root", kind="contains")],
        )


class _CrashPlugin(AnalyzerPlugin):
    name = "fake.crash"
    version = "0.1.0"

    def detect(self, repo_path: Path) -> bool:
        return True

    def analyze(self, repo_path: Path, project_id: str) -> AnalyzerResult:
        raise RuntimeError("boom")


class _SkipPlugin(AnalyzerPlugin):
    name = "fake.skip"
    version = "0.1.0"

    def detect(self, repo_path: Path) -> bool:
        return False  # NOT_APPLICABLE

    def analyze(self, repo_path: Path, project_id: str) -> AnalyzerResult:  # pragma: no cover
        return AnalyzerResult()


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


def test_ingest_end_to_end(tmp_path: Path) -> None:
    clear_registry()
    register_plugin(_OkPlugin())
    store = tmp_path / "g.sqlite"
    report = ingest_project(tmp_path, "demo", store_path=store)

    assert "fake.ok" in report.ingested
    assert report.summaries["fake.ok"]["nodes"] == 1

    conn = open_store("demo", path=store)
    got = load_graph(conn, "demo", plugin="fake.ok")
    conn.close()
    assert len(got.nodes) == 1
    assert len(got.edges) == 1
    # 注: registry 还发现内置 builtin.cross_link; 缺库时它返空 result 也会入库 (不报错)


def test_ingest_isolates_crash(tmp_path: Path) -> None:
    clear_registry()
    register_plugin(_OkPlugin())
    register_plugin(_CrashPlugin())
    store = tmp_path / "g.sqlite"
    report = ingest_project(tmp_path, "demo", store_path=store)

    assert "fake.ok" in report.ingested
    assert "fake.crash" not in report.ingested  # 崩溃插件被滤掉


def test_ingest_skips_not_applicable(tmp_path: Path) -> None:
    clear_registry()
    register_plugin(_SkipPlugin())
    store = tmp_path / "g.sqlite"
    report = ingest_project(tmp_path, "demo", store_path=store)
    assert "fake.skip" not in report.ingested


def test_ingest_idempotent(tmp_path: Path) -> None:
    clear_registry()
    register_plugin(_OkPlugin())
    store = tmp_path / "g.sqlite"
    ingest_project(tmp_path, "demo", store_path=store)
    ingest_project(tmp_path, "demo", store_path=store)  # 重跑

    conn = open_store("demo", path=store)
    got = load_graph(conn, "demo", plugin="fake.ok")
    conn.close()
    assert len(got.nodes) == 1  # 不翻倍


def test_ingest_project_isolation(tmp_path: Path) -> None:
    clear_registry()
    register_plugin(_OkPlugin())
    store_a = tmp_path / "a.sqlite"
    store_b = tmp_path / "b.sqlite"
    ingest_project(tmp_path, "proj-a", store_path=store_a)

    conn_b = open_store("proj-b", path=store_b)
    got_b = load_graph(conn_b, "proj-b", plugin="fake.ok")
    conn_b.close()
    assert got_b.nodes == []  # b 的 store 空

    conn_a = open_store("proj-a", path=store_a)
    got_a = load_graph(conn_a, "proj-a", plugin="fake.ok")
    conn_a.close()
    assert got_a.nodes[0].project_id == "proj-a"
