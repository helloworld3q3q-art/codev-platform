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
from codev_platform.graph.store import open_store
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
    got = conn.load_graph("demo", plugin="fake.ok")
    conn.close()
    assert len(got.nodes) == 1
    assert len(got.edges) == 1


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
    got = conn.load_graph("demo", plugin="fake.ok")
    conn.close()
    assert len(got.nodes) == 1  # 不翻倍


def test_ingest_project_isolation(tmp_path: Path) -> None:
    clear_registry()
    register_plugin(_OkPlugin())
    store_a = tmp_path / "a.sqlite"
    store_b = tmp_path / "b.sqlite"
    ingest_project(tmp_path, "proj-a", store_path=store_a)

    conn_b = open_store("proj-b", path=store_b)
    got_b = conn_b.load_graph("proj-b", plugin="fake.ok")
    conn_b.close()
    assert got_b.nodes == []  # b 的 store 空

    conn_a = open_store("proj-a", path=store_a)
    got_a = conn_a.load_graph("proj-a", plugin="fake.ok")
    conn_a.close()
    assert got_a.nodes[0].project_id == "proj-a"


# ---- 多根 ingest (前后端分离/N前端M后端 同一逻辑项目跨仓) ----

class _RepoNodePlugin(AnalyzerPlugin):
    """按 repo basename 产唯一节点 —— 验多仓合并不互相覆盖。"""
    name = "fake.reponode"
    version = "1.0.0"

    def detect(self, repo_path: Path) -> bool:
        return True

    def analyze(self, repo_path: Path, project_id: str) -> AnalyzerResult:
        base = Path(repo_path).name
        return AnalyzerResult(
            plugin="fake.reponode",
            nodes=[GraphNode(id=f"{project_id}:project:{base}", kind="project",
                             name=base, project_id=project_id)],
        )


def test_resolve_repos_config_and_dedup(tmp_path, monkeypatch):
    from codev_platform.graph.ingest import _resolve_repos
    main = tmp_path / "main"; main.mkdir()
    extra = tmp_path / "extra"; extra.mkdir()
    # 显式 extra_repos
    assert _resolve_repos(main, "p", [str(extra)]) == [main.resolve(), extra.resolve()]
    # config 驱动 (extra_repos=None 读 config; 解析在 core.repos)
    monkeypatch.setattr("codev_platform.core.repos.load_config",
                        lambda: {"projects": {"p": {"extra_repos": [str(extra)]}}})
    assert extra.resolve() in _resolve_repos(main, "p", None)
    # 不存在的目录被丢弃
    assert _resolve_repos(main, "p", [str(tmp_path / "nope")]) == [main.resolve()]
    # 去重 (extra == main)
    assert _resolve_repos(main, "p", [str(main)]) == [main.resolve()]


def test_resolve_meta_extra_entries_project_id_and_path():
    # meta.json extra_repos: project-id 引用解析成其 repo_path(可移植); 字面路径透传; 空项跳过。
    from codev_platform.graph.ingest import _resolve_meta_extra_entries
    cfg = {"projects": {"ideas-pda-app": {"repo_path": "/x/pda"}}}
    assert _resolve_meta_extra_entries(["ideas-pda-app", "/abs/other", "", "  "], cfg) == ["/x/pda", "/abs/other"]
    # 未登记 project-id → 当字面值(不丢)
    assert _resolve_meta_extra_entries(["unknown-proj"], {"projects": {}}) == ["unknown-proj"]
    assert _resolve_meta_extra_entries([], cfg) == []


def test_meta_extra_repos_absent_returns_empty():
    # 不存在的 project → 无 meta.json → [](优雅降级, 回退用户 config)。
    from codev_platform.graph.ingest import _meta_extra_repos
    assert _meta_extra_repos("no-such-project-xyz", {"projects": {}}) == []


def test_resolve_repos_merges_meta_extra(tmp_path, monkeypatch):
    # meta.json 声明的可移植 extra_repos 与用户 config 合并(git 版本化跨仓关系换机不丢)。
    from codev_platform.graph import ingest as _ing
    main = tmp_path / "main"; main.mkdir()
    pda = tmp_path / "pda"; pda.mkdir()
    monkeypatch.setattr("codev_platform.core.repos.load_config", lambda: {"projects": {}})
    monkeypatch.setattr("codev_platform.core.repos.meta_extra_repos", lambda pid, cfg: [str(pda)])
    assert pda.resolve() in _ing._resolve_repos(main, "ideas-v2", None)


def test_multiroot_merges_both_repos_no_overwrite(tmp_path):
    clear_registry()
    register_plugin(_RepoNodePlugin())
    main = tmp_path / "main"; main.mkdir()
    extra = tmp_path / "extra"; extra.mkdir()
    store = tmp_path / "g.sqlite"
    ingest_project(main, "demo", store_path=store, extra_repos=[str(extra)])
    s = open_store("demo", path=store)
    m = s.load_graph("demo")
    s.close()
    ids = {n.id for n in m.nodes}
    # 两仓节点都在 store(同插件跑两仓未互相 upsert 覆盖)
    assert "demo:project:main" in ids and "demo:project:extra" in ids
