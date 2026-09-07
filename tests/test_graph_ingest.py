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

from codev_platform.graph.ingest import IngestFailure, ingest_project
from codev_platform.graph.schema import AnalyzerResult, EdgeKind, GraphEdge, GraphNode, NodeKind
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
    assert report.failures == [
        IngestFailure(
            phase="plugin",
            component="fake.crash",
            code="ANALYZE_FAILED",
        )
    ]
    assert "boom" not in repr(report.failures)


def test_ingest_skips_not_applicable(tmp_path: Path) -> None:
    clear_registry()
    register_plugin(_SkipPlugin())
    store = tmp_path / "g.sqlite"
    report = ingest_project(tmp_path, "demo", store_path=store)
    assert "fake.skip" not in report.ingested
    assert report.failures == []


def test_ingest_clears_stale_not_applicable_plugin(tmp_path: Path) -> None:
    clear_registry()
    store = tmp_path / "g.sqlite"
    stale = GraphNode(
        id="demo:project:stale",
        kind="project",
        name="stale",
        project_id="demo",
    )
    conn = open_store("demo", path=store)
    conn.upsert_result("demo", AnalyzerResult(nodes=[stale], plugin="fake.skip"))
    conn.close()

    register_plugin(_SkipPlugin())
    ingest_project(tmp_path, "demo", store_path=store)

    conn = open_store("demo", path=store)
    try:
        got = conn.load_graph("demo", plugin="fake.skip")
    finally:
        conn.close()
    assert got.nodes == []


def test_ingest_keeps_stale_data_when_plugin_crashes(tmp_path: Path) -> None:
    clear_registry()
    store = tmp_path / "g.sqlite"
    stale = GraphNode(
        id="demo:project:stale-crash",
        kind="project",
        name="stale-crash",
        project_id="demo",
    )
    conn = open_store("demo", path=store)
    conn.upsert_result("demo", AnalyzerResult(nodes=[stale], plugin="fake.crash"))
    conn.close()

    register_plugin(_CrashPlugin())
    ingest_project(tmp_path, "demo", store_path=store)

    conn = open_store("demo", path=store)
    try:
        got = conn.load_graph("demo", plugin="fake.crash")
    finally:
        conn.close()
    assert [n.id for n in got.nodes] == ["demo:project:stale-crash"]


class _PartialCrashPlugin(AnalyzerPlugin):
    name = "fake.partial"
    version = "1.0.0"

    def detect(self, repo_path: Path) -> bool:
        return True

    def analyze(self, repo_path: Path, project_id: str) -> AnalyzerResult:
        base = Path(repo_path).name
        if base == "extra":
            raise RuntimeError("boom-extra")
        return AnalyzerResult(
            plugin=self.name,
            nodes=[
                GraphNode(
                    id=f"{project_id}:project:{base}-new",
                    kind="project",
                    name=f"{base}-new",
                    project_id=project_id,
                )
            ],
        )


def test_ingest_keeps_stale_data_when_one_repo_crashes_and_other_succeeds(tmp_path: Path) -> None:
    clear_registry()
    main = tmp_path / "main"
    main.mkdir()
    extra = tmp_path / "extra"
    extra.mkdir()
    store = tmp_path / "g.sqlite"
    old_nodes = [
        GraphNode(id="demo:project:main-old", kind="project", name="main-old", project_id="demo"),
        GraphNode(id="demo:project:extra-old", kind="project", name="extra-old", project_id="demo"),
    ]
    conn = open_store("demo", path=store)
    conn.upsert_result("demo", AnalyzerResult(nodes=old_nodes, plugin="fake.partial"))
    conn.close()

    register_plugin(_PartialCrashPlugin())
    report = ingest_project(main, "demo", store_path=store, extra_repos=[str(extra)])

    conn = open_store("demo", path=store)
    try:
        got = conn.load_graph("demo", plugin="fake.partial")
    finally:
        conn.close()
    assert {n.id for n in got.nodes} == {"demo:project:main-old", "demo:project:extra-old"}
    assert report.summaries["fake.partial"]["partial_failure_kept_stale"] is True


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
    main = tmp_path / "main"
    main.mkdir()
    extra = tmp_path / "extra"
    extra.mkdir()
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
    cfg = {"projects": {"sample-project-alpha": {"repo_path": "/x/pda"}}}
    assert _resolve_meta_extra_entries(["sample-project-alpha", "/abs/other", "", "  "], cfg) == ["/x/pda", "/abs/other"]
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
    main = tmp_path / "main"
    main.mkdir()
    pda = tmp_path / "pda"
    pda.mkdir()
    monkeypatch.setattr("codev_platform.core.repos.load_config",
                        lambda: {"projects": {"sample-project-alpha": {"repo_path": str(pda)}}})
    monkeypatch.setattr("codev_platform.core.repos._read_meta",
                        lambda pid: {"extra_repos": ["sample-project-alpha"]})
    assert pda.resolve() in _ing._resolve_repos(main, "sample-project-beta", None)


def test_project_repo_roots_skips_relative_extra_fail_closed(tmp_path, monkeypatch):
    # 相对 extra_repos 按进程 CWD 解析 = 不确定 + 把 CWD 下偶然同名目录拉进可读白名单(项目隔离风险)
    # → fail-closed 丢弃, 只认绝对路径 / 已登记 project-id ref。
    from codev_platform.core.repos import project_repo_roots
    main = tmp_path / "main"
    main.mkdir()
    absx = tmp_path / "absx"
    absx.mkdir()
    monkeypatch.setattr("codev_platform.core.repos.load_config",
                        lambda: {"projects": {"p": {"extra_repos": [str(absx), "sneaky-rel"]}}})
    monkeypatch.setattr("codev_platform.core.repos.meta_extra_repos", lambda pid, cfg: [])
    roots = project_repo_roots("p", main_repo=main)
    assert main.resolve() in roots
    assert absx.resolve() in roots                              # 绝对路径正常纳入
    assert all("sneaky-rel" not in str(r) for r in roots)      # 相对项被 fail-closed 丢弃


def test_project_repo_specs_keep_main_untagged_and_tag_extras(tmp_path, monkeypatch):
    from codev_platform.core.repos import project_repo_specs
    main = tmp_path / "main"
    main.mkdir()
    absx = tmp_path / "absx"
    absx.mkdir()
    pda = tmp_path / "pda"
    pda.mkdir()

    monkeypatch.setattr(
        "codev_platform.core.repos._read_meta",
        lambda pid: {"extra_repos": ["pda-proj"]} if pid == "demo" else {},
    )
    cfg = {
        "projects": {
            "demo": {"extra_repos": [str(absx)]},
            "pda-proj": {"repo_path": str(pda)},
        }
    }
    specs = project_repo_specs("demo", main_repo=main, cfg=cfg)

    assert [s.root for s in specs] == [main.resolve(), absx.resolve(), pda.resolve()]
    assert [s.tag for s in specs] == ["", "absx", "pda"]
    assert specs[0].is_main is True and specs[0].local_ref("node-1") == "node-1"
    assert specs[2].source_project_id == "pda-proj"
    assert specs[2].local_ref("node-1") == "pda::node-1"
    assert specs[2].local_file("src/app.ts") == "pda::src/app.ts"


def test_project_repo_specs_dedupes_extra_basename_tags(tmp_path):
    from codev_platform.core.repos import project_repo_specs
    main = tmp_path / "main"
    main.mkdir()
    a = tmp_path / "a" / "dup"
    a.mkdir(parents=True)
    b = tmp_path / "b" / "dup"
    b.mkdir(parents=True)
    cfg = {"projects": {"demo": {"extra_repos": [str(a), str(b)]}}}

    specs = project_repo_specs("demo", main_repo=main, cfg=cfg)

    assert [s.tag for s in specs] == ["", "dup", "dup-2"]


def test_impacted_project_ids_for_repo_includes_extra_parent_by_path(tmp_path, monkeypatch):
    from codev_platform.core.repos import impacted_project_ids_for_repo
    parent = tmp_path / "parent"
    parent.mkdir()
    child = tmp_path / "child"
    child.mkdir()
    monkeypatch.setattr("codev_platform.core.repos._read_meta", lambda pid: {})
    cfg = {
        "projects": {
            "parent-proj": {"repo_path": str(parent), "extra_repos": [str(child)]},
            "child-proj": {"repo_path": str(child)},
        }
    }

    assert impacted_project_ids_for_repo(
        child, primary_project_id="child-proj", cfg=cfg
    ) == ["child-proj", "parent-proj"]


def test_impacted_project_ids_for_repo_includes_parent_by_project_ref(tmp_path, monkeypatch):
    from codev_platform.core.repos import impacted_project_ids_for_repo
    child = tmp_path / "child"
    child.mkdir()
    monkeypatch.setattr(
        "codev_platform.core.repos._read_meta",
        lambda pid: {"extra_repos": ["child-proj"]} if pid == "parent-proj" else {},
    )
    cfg = {"projects": {"parent-proj": {}, "child-proj": {"repo_path": str(child)}}}

    assert impacted_project_ids_for_repo(
        child, primary_project_id="child-proj", cfg=cfg
    ) == ["child-proj", "parent-proj"]


def test_webhook_extra_repo_mapping_issues_for_project_ref(tmp_path, monkeypatch):
    from codev_platform.core.repos import webhook_extra_repo_mapping_issues
    child = tmp_path / "child"
    child.mkdir()
    monkeypatch.setattr("codev_platform.core.repos._read_meta", lambda pid: {})
    cfg = {
        "projects": {
            "parent-proj": {"extra_repos": ["child-proj"]},
            "child-proj": {"repo_path": str(child)},
        }
    }

    issues = webhook_extra_repo_mapping_issues(cfg)

    assert issues == [{
        "project_id": "parent-proj",
        "extra": "child-proj",
        "reason": "extra project 缺 projects.<extra>.webhook_repo",
    }]


def test_webhook_extra_repo_mapping_issues_ok_when_child_has_webhook(tmp_path, monkeypatch):
    from codev_platform.core.repos import webhook_extra_repo_mapping_issues
    child = tmp_path / "child"
    child.mkdir()
    monkeypatch.setattr("codev_platform.core.repos._read_meta", lambda pid: {})
    cfg = {
        "projects": {
            "parent-proj": {"extra_repos": ["child-proj"]},
            "child-proj": {"repo_path": str(child), "webhook_repo": "org/child"},
        }
    }

    assert webhook_extra_repo_mapping_issues(cfg) == []


def test_webhook_extra_repo_mapping_issues_dedupes_same_path_from_config_and_meta(tmp_path, monkeypatch):
    from codev_platform.core.repos import webhook_extra_repo_mapping_issues
    child = tmp_path / "child"
    child.mkdir()
    child_alt = str(child).replace("\\", "/")
    monkeypatch.setattr(
        "codev_platform.core.repos._read_meta",
        lambda pid: {"extra_repos": [child_alt]} if pid == "parent-proj" else {},
    )
    cfg = {"projects": {"parent-proj": {"extra_repos": [str(child)]}}}

    issues = webhook_extra_repo_mapping_issues(cfg, project_ids=["parent-proj"])

    assert len(issues) == 1
    assert issues[0]["extra"] == str(child)


def test_webhook_extra_repo_mapping_issues_can_scan_webhook_enabled_projects_only(monkeypatch):
    from codev_platform.core.repos import webhook_enabled_project_ids, webhook_extra_repo_mapping_issues
    monkeypatch.setattr(
        "codev_platform.core.repos._read_meta",
        lambda pid: {"extra_repos": ["meta-child"]} if pid == "meta-parent" else {},
    )
    cfg = {"projects": {"active-proj": {}, "hooked-proj": {"webhook_repo": "org/hooked"}}}

    assert webhook_extra_repo_mapping_issues(cfg, project_ids=webhook_enabled_project_ids(cfg)) == []


def test_webhook_enabled_project_ids_skips_configured_project_without_webhook(monkeypatch):
    from codev_platform.core.repos import webhook_enabled_project_ids, webhook_extra_repo_mapping_issues
    monkeypatch.setattr(
        "codev_platform.core.repos._read_meta",
        lambda pid: {"extra_repos": ["/abs/child"]} if pid == "parent-proj" else {},
    )
    cfg = {"projects": {"parent-proj": {"repo_path": "/abs/parent"}}}

    assert webhook_enabled_project_ids(cfg) == []
    assert webhook_extra_repo_mapping_issues(cfg, project_ids=webhook_enabled_project_ids(cfg)) == []


def test_webhook_enabled_parent_still_warns_for_unmapped_extra(tmp_path, monkeypatch):
    from codev_platform.core.repos import webhook_enabled_project_ids, webhook_extra_repo_mapping_issues
    child = tmp_path / "child"
    child.mkdir()
    monkeypatch.setattr("codev_platform.core.repos._read_meta", lambda pid: {})
    cfg = {
        "projects": {
            "parent-proj": {
                "repo_path": str(tmp_path / "parent"),
                "webhook_repo": "org/parent",
                "extra_repos": [str(child)],
            }
        }
    }

    issues = webhook_extra_repo_mapping_issues(cfg, project_ids=webhook_enabled_project_ids(cfg))

    assert len(issues) == 1
    assert issues[0]["project_id"] == "parent-proj"
    assert issues[0]["extra"] == str(child)


def test_webhook_extra_repo_mapping_issues_dedupes_same_child_by_path_and_project_ref(tmp_path, monkeypatch):
    from codev_platform.core.repos import webhook_extra_repo_mapping_issues
    child = tmp_path / "child"
    child.mkdir()
    monkeypatch.setattr(
        "codev_platform.core.repos._read_meta",
        lambda pid: {"extra_repos": ["child-proj"]} if pid == "parent-proj" else {},
    )
    cfg = {
        "projects": {
            "parent-proj": {"extra_repos": [str(child)]},
            "child-proj": {"repo_path": str(child)},
        }
    }

    issues = webhook_extra_repo_mapping_issues(cfg, project_ids=["parent-proj"])

    assert issues == [{
        "project_id": "parent-proj",
        "extra": str(child),
        "reason": "mapped project child-proj 缺 webhook_repo",
    }]


def test_multiroot_merges_both_repos_no_overwrite(tmp_path):
    clear_registry()
    register_plugin(_RepoNodePlugin())
    main = tmp_path / "main"
    main.mkdir()
    extra = tmp_path / "extra"
    extra.mkdir()
    store = tmp_path / "g.sqlite"
    ingest_project(main, "demo", store_path=store, extra_repos=[str(extra)])
    s = open_store("demo", path=store)
    m = s.load_graph("demo")
    s.close()
    ids = {n.id for n in m.nodes}
    # 两仓节点都在 store(同插件跑两仓未互相 upsert 覆盖)
    assert "demo:project:main" in ids and "demo:project:extra" in ids


class _FrontendCollidePlugin(AnalyzerPlugin):
    """两仓都产同 id 的前端节点(模拟各自 src/pages/index.vue)→ 验 RepoScope 仓内唯一化不丢后仓。"""
    name = "fake.frontcollide"
    version = "1.0.0"

    def detect(self, repo_path: Path) -> bool:
        return True

    def analyze(self, repo_path: Path, project_id: str) -> AnalyzerResult:
        nid = f"{project_id}:frontend_module:src/pages/index.vue"
        return AnalyzerResult(
            plugin="fake.frontcollide",
            nodes=[GraphNode(id=nid, kind=NodeKind.FRONTEND_MODULE.value, name="index",
                             project_id=project_id, file="src/pages/index.vue")],
        )


def test_multiroot_frontend_id_collision_both_survive(tmp_path):
    # 两仓同相对路径前端节点 id 相同 → 没有仓维度时 merge first-wins 静默丢后仓。RepoScope 给 extra
    # 仓打 tag 仓内唯一化, 两节点都该进 store(根治 P1b)。主仓 tag='' 原 id 不变。
    clear_registry()
    register_plugin(_FrontendCollidePlugin())
    main = tmp_path / "web"
    main.mkdir()
    pda = tmp_path / "pda"
    pda.mkdir()
    store = tmp_path / "g.sqlite"
    ingest_project(main, "demo", store_path=store, extra_repos=[str(pda)])
    s = open_store("demo", path=store)
    mod_ids = {n.id for n in s.load_graph("demo").nodes
               if n.kind == NodeKind.FRONTEND_MODULE.value}
    s.close()
    assert "demo:frontend_module:src/pages/index.vue" in mod_ids       # 主仓 tag='' 原样
    assert "pda::demo:frontend_module:src/pages/index.vue" in mod_ids  # extra 仓打 tag, 未被丢
    assert len(mod_ids) == 2


class _FrontendApiCollidePlugin(AnalyzerPlugin):
    """两仓都产同 file 的 api_call, 验 frontend_deps post-pass 同步 localize 后桥接不丢。"""
    name = "fake.frontapi"
    version = "1.0.0"

    def detect(self, repo_path: Path) -> bool:
        return True

    def analyze(self, repo_path: Path, project_id: str) -> AnalyzerResult:
        rel = "src/pages/index.vue"
        return AnalyzerResult(
            plugin=self.name,
            nodes=[
                GraphNode(
                    id=f"{project_id}:frontend_api_call:{rel}:load",
                    kind=NodeKind.FRONTEND_API_CALL.value,
                    name="load",
                    project_id=project_id,
                    file=rel,
                    meta={"url": "/api/load", "http_method": "POST"},
                )
            ],
        )


def test_multiroot_frontend_deps_localized_before_bridge(tmp_path, monkeypatch):
    # frontend_deps 是 ingest post-pass, 不走 run_applicable 的 merge/localize。它也必须经 RepoScope
    # 打仓 tag, 否则 extra 仓的 api_call(file 已带 tag) 找不到同文件 module, frontend_bridge=0。
    clear_registry()
    register_plugin(_FrontendApiCollidePlugin())
    main = tmp_path / "web"
    main.mkdir()
    pda = tmp_path / "pda"
    pda.mkdir()
    rel = "src/pages/index.vue"

    def _fake_scan(repo_path: Path, project_id: str):
        dep_rel = "src/shared/helper.ts"
        mod = GraphNode(
            id=f"{project_id}:frontend_component:{rel}",
            kind=NodeKind.FRONTEND_MODULE.value,
            name="index",
            project_id=project_id,
            file=rel,
        )
        dep = GraphNode(
            id=f"{project_id}:frontend_component:{dep_rel}",
            kind=NodeKind.FRONTEND_MODULE.value,
            name="helper",
            project_id=project_id,
            file=dep_rel,
        )
        edge = GraphEdge(source=mod.id, target=dep.id, kind=EdgeKind.IMPORTS.value)
        return [mod, dep], [edge]

    monkeypatch.setattr(
        "codev_platform.plugins.builtin._stack_scan.scan_frontend_deps", _fake_scan
    )
    store = tmp_path / "g.sqlite"
    ingest_project(main, "demo", store_path=store, extra_repos=[str(pda)])

    s = open_store("demo", path=store)
    try:
        graph = s.load_graph("demo")
    finally:
        s.close()

    contains = {(e.source, e.target) for e in graph.edges if e.kind == EdgeKind.CONTAINS.value}
    assert (
        "demo:frontend_component:src/pages/index.vue",
        "demo:frontend_api_call:src/pages/index.vue:load",
    ) in contains
    assert (
        "pda::demo:frontend_component:src/pages/index.vue",
        "pda::demo:frontend_api_call:src/pages/index.vue:load",
    ) in contains
    imports = {(e.source, e.target) for e in graph.edges if e.kind == EdgeKind.IMPORTS.value}
    assert (
        "pda::demo:frontend_component:src/pages/index.vue",
        "pda::demo:frontend_component:src/shared/helper.ts",
    ) in imports
