"""调用边 resolver 框架测试 —— 覆盖 _calls_pass 三大卖点 + 路由 + codegraph 升格判定。

新增框架(graph/call_resolvers/)把单一 codegraph 桥接升格为按语言栈可扩展的 resolver 层。
本测试不碰真 codegraph / 真插件, 用假 resolver monkeypatch 注册表, 验证:
- applicable_resolvers 路由 + applies 抛错 fail-soft 滤掉
- _calls_pass 全局去重「先跑者赢」+ by_resolver 计数
- 单 resolver resolve 抛错被隔离, 不拖垮其余 + 整个 pass
- CodegraphCallResolver.applies 判定(有 endpoint+function 才适用)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from codev_platform.graph import call_resolvers
from codev_platform.graph.call_resolvers import (
    applicable_resolvers,
    register_resolver,
    registered_resolvers,
)
from codev_platform.graph.call_resolvers.base import CallResolver
from codev_platform.graph.call_resolvers.codegraph import CodegraphCallResolver
from codev_platform.graph.ingest import CALLS_PLUGIN, IngestReport, _calls_pass
from codev_platform.graph.schema import EdgeKind, GraphEdge, GraphNode, NodeKind
from codev_platform.graph.store import load_graph, open_store

_CALLS = EdgeKind.CALLS.value


def _edge(pid: str, fn: str) -> GraphEdge:
    return GraphEdge(
        source=f"{pid}:backend_endpoint:e1",
        target=f"{pid}:backend_function:{fn}",
        kind=_CALLS,
    )


class _FakeResolver:
    """可配置假 resolver:控制 applies 结果 / resolve 产边 / 是否抛错。"""

    def __init__(self, name: str, edges: list[GraphEdge], *,
                 applies_result: object = True, raise_resolve: bool = False) -> None:
        self.name = name
        self._edges = edges
        self._applies_result = applies_result  # True / False / "raise"
        self._raise_resolve = raise_resolve

    def applies(self, repo: Path, nodes: list[GraphNode]) -> bool:
        if self._applies_result == "raise":
            raise RuntimeError("applies boom")
        return bool(self._applies_result)

    def resolve(self, repo: Path, project_id: str, nodes: list[GraphNode]) -> list[GraphEdge]:
        if self._raise_resolve:
            raise RuntimeError("resolve boom")
        return list(self._edges)


@pytest.fixture
def isolated_registry(monkeypatch):
    """隔离 _RESOLVERS:每个用例从空表起, 不污染真实注册的 codegraph resolver。"""
    fresh: list[CallResolver] = []
    monkeypatch.setattr(call_resolvers.base, "_RESOLVERS", fresh)
    return fresh


# ---- 协议 / 注册表 ----

def test_codegraph_resolver_satisfies_protocol():
    # runtime_checkable Protocol: 内置 resolver 必须结构匹配。
    assert isinstance(CodegraphCallResolver(), CallResolver)


def test_register_and_list(isolated_registry):
    r = _FakeResolver("x", [])
    register_resolver(r)
    assert [x.name for x in registered_resolvers()] == ["x"]


def test_codegraph_registered_by_default():
    # 包 import 时 codegraph resolver 自动注册(不经 isolated_registry, 看真实表)。
    assert any(r.name == "codegraph" for r in registered_resolvers())


# ---- 路由 + applies fail-soft ----

def test_applicable_filters_by_applies(isolated_registry):
    register_resolver(_FakeResolver("yes", [], applies_result=True))
    register_resolver(_FakeResolver("no", [], applies_result=False))
    names = [r.name for r in applicable_resolvers(Path("."), [])]
    assert names == ["yes"]


def test_applies_raising_is_treated_not_applicable(isolated_registry):
    register_resolver(_FakeResolver("boom", [], applies_result="raise"))
    register_resolver(_FakeResolver("ok", [], applies_result=True))
    # applies 抛错 → 视为不适用(fail-soft), 不拖垮其余探测。
    names = [r.name for r in applicable_resolvers(Path("."), [])]
    assert names == ["ok"]


# ---- codegraph 升格判定 ----

def test_codegraph_applies_needs_endpoint_and_function():
    r = CodegraphCallResolver()
    ep = GraphNode(id="p:backend_endpoint:e", kind=NodeKind.BACKEND_ENDPOINT.value,
                   name="e", project_id="p")
    fn = GraphNode(id="p:backend_function:f", kind=NodeKind.BACKEND_FUNCTION.value,
                   name="f", project_id="p")
    assert r.applies(Path("."), [ep, fn]) is True
    assert r.applies(Path("."), [ep]) is False   # 缺 function
    assert r.applies(Path("."), [fn]) is False   # 缺 endpoint
    assert r.applies(Path("."), []) is False


# ---- _calls_pass 去重 + 隔离(端到端经真 store) ----

def test_calls_pass_dedup_first_wins(isolated_registry, tmp_path):
    pid = "proj-dedup"
    # 两个 resolver 产**同一条**边 + first 多产一条独有边。
    register_resolver(_FakeResolver("first", [_edge(pid, "f1"), _edge(pid, "f2")]))
    register_resolver(_FakeResolver("second", [_edge(pid, "f1")]))  # f1 与 first 重复

    conn = open_store(pid, path=tmp_path / "store.sqlite")
    try:
        report = IngestReport(project_id=pid)
        _calls_pass(conn, pid, report, tmp_path)
        summary = report.summaries[CALLS_PLUGIN]
        # 全局去重: f1 + f2 = 2 条(second 的 f1 被吞)。
        assert summary["calls_edges"] == 2
        # 先跑者赢: first 占 f1+f2=2, second 的唯一边重复 → 0。
        assert summary["by_resolver"] == {"first": 2, "second": 0}
        # 真落库可读回。
        merged = load_graph(conn, pid)
        calls = [e for e in merged.edges if e.kind == _CALLS]
        assert len(calls) == 2
    finally:
        conn.close()


def test_calls_pass_resolver_failsoft(isolated_registry, tmp_path):
    pid = "proj-failsoft"
    register_resolver(_FakeResolver("crash", [], raise_resolve=True))
    register_resolver(_FakeResolver("healthy", [_edge(pid, "f1")]))

    conn = open_store(pid, path=tmp_path / "store.sqlite")
    try:
        report = IngestReport(project_id=pid)
        _calls_pass(conn, pid, report, tmp_path)  # crash 抛错不应中断
        summary = report.summaries[CALLS_PLUGIN]
        assert summary["by_resolver"]["crash"] == 0   # 异常记 0, 不漏入库
        assert summary["by_resolver"]["healthy"] == 1
        assert summary["calls_edges"] == 1
    finally:
        conn.close()
