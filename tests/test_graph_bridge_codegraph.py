"""A1 codegraph 桥接单测 (graph/bridge_codegraph.py)。

造一个临时 codegraph sqlite (nodes/edges) + store 的 endpoint/function 节点,
验证桥接按 calls 调用图 + (file,name) join 把 backend_endpoint --calls--> backend_function
物化出来;并验深度受限 / 无匹配 / codegraph 缺失 fail-soft。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from codev_platform.graph.bridge_codegraph import bridge_endpoints_to_functions
from codev_platform.graph.schema import EdgeKind, GraphNode, NodeKind


def _make_codegraph(path: Path, nodes: list[tuple], edges: list[tuple]) -> None:
    """nodes: (id, name, file_path, kind); edges: (source, target, kind)。"""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE nodes (id TEXT, name TEXT, file_path TEXT, kind TEXT)")
    conn.execute("CREATE TABLE edges (source TEXT, target TEXT, kind TEXT)")
    conn.executemany("INSERT INTO nodes VALUES (?,?,?,?)", nodes)
    conn.executemany("INSERT INTO edges VALUES (?,?,?)", edges)
    conn.commit()
    conn.close()


def _endpoint(handler: str, file: str, meta_extra: dict | None = None) -> GraphNode:
    meta = {"handler": handler}
    if meta_extra:
        meta.update(meta_extra)
    return GraphNode(
        id=f"p:backend_endpoint:POST:/{handler}", kind=NodeKind.BACKEND_ENDPOINT.value,
        name=handler, project_id="p", file=file, meta=meta,
    )


def _func(name: str, file: str) -> GraphNode:
    return GraphNode(
        id=f"p:backend_function:{file}:{name}", kind=NodeKind.BACKEND_FUNCTION.value,
        name=name, project_id="p", file=file,
    )


def test_bridge_materializes_endpoint_to_function_via_calls(tmp_path):
    # handler create_user (api.py) --calls--> save_user (repo.py, 碰表)
    cg = tmp_path / "codegraph.db"
    _make_codegraph(
        cg,
        nodes=[
            ("cg:create_user", "create_user", "app/api.py", "function"),
            ("cg:save_user", "save_user", "app/repo.py", "function"),
        ],
        edges=[("cg:create_user", "cg:save_user", "calls")],
    )
    ep = _endpoint("create_user", "app/api.py")
    fn = _func("save_user", "app/repo.py")

    edges = bridge_endpoints_to_functions("p", [ep], [fn], codegraph_db=cg)

    assert len(edges) == 1
    e = edges[0]
    assert e.source == ep.id and e.target == fn.id
    assert e.kind == EdgeKind.CALLS.value
    assert e.meta.get("bridge") == "codegraph"


def test_bridge_transitive_through_service_layer(tmp_path):
    # handler --calls--> service --calls--> repo(碰表): 多跳可达
    cg = tmp_path / "codegraph.db"
    _make_codegraph(
        cg,
        nodes=[
            ("cg:h", "handle", "api.py", "function"),
            ("cg:s", "service", "svc.py", "function"),
            ("cg:r", "repo", "repo.py", "method"),
        ],
        edges=[("cg:h", "cg:s", "calls"), ("cg:s", "cg:r", "calls")],
    )
    ep = _endpoint("handle", "api.py")
    fn = _func("repo", "repo.py")
    edges = bridge_endpoints_to_functions("p", [ep], [fn], codegraph_db=cg)
    assert len(edges) == 1 and edges[0].target == fn.id


def test_bridge_no_edge_when_callee_not_a_backend_function(tmp_path):
    # handler 调到的函数不在 store backend_function 集合 -> 不挂边
    cg = tmp_path / "codegraph.db"
    _make_codegraph(
        cg,
        nodes=[("cg:h", "handle", "api.py", "function"),
               ("cg:x", "helper", "util.py", "function")],
        edges=[("cg:h", "cg:x", "calls")],
    )
    ep = _endpoint("handle", "api.py")
    fn = _func("save_user", "repo.py")  # 与 helper 无关
    edges = bridge_endpoints_to_functions("p", [ep], [fn], codegraph_db=cg)
    assert edges == []


def test_bridge_failsoft_when_codegraph_missing(tmp_path):
    ep = _endpoint("handle", "api.py")
    fn = _func("save_user", "repo.py")
    # 不存在的 codegraph.db -> INDEX_MISSING -> fail-soft 返回 []
    edges = bridge_endpoints_to_functions("p", [ep], [fn], codegraph_db=tmp_path / "nope.db")
    assert edges == []


def test_bridge_empty_inputs():
    assert bridge_endpoints_to_functions("p", [], [], codegraph_db=None) == []


def test_bridge_skips_ambiguous_cross_file_handler(tmp_path):
    # handler "handle" 在两个文件都有, 都不等于 ep_file -> 歧义放弃挂边 (宁缺毋滥)
    cg = tmp_path / "codegraph.db"
    _make_codegraph(
        cg,
        nodes=[("cg:h1", "handle", "x.py", "function"),
               ("cg:h2", "handle", "y.py", "function"),
               ("cg:r", "repo", "repo.py", "function")],
        edges=[("cg:h1", "cg:r", "calls")],
    )
    ep = _endpoint("handle", "api.py")  # 与两个 handler 文件都不同
    fn = _func("repo", "repo.py")
    assert bridge_endpoints_to_functions("p", [ep], [fn], codegraph_db=cg) == []


def test_bridge_unique_cross_file_handler_still_links(tmp_path):
    # handler 唯一同名但跨文件 -> 仍挂边 (安全)
    cg = tmp_path / "codegraph.db"
    _make_codegraph(
        cg,
        nodes=[("cg:h", "handle", "x.py", "function"), ("cg:r", "repo", "repo.py", "function")],
        edges=[("cg:h", "cg:r", "calls")],
    )
    ep = _endpoint("handle", "api.py")
    fn = _func("repo", "repo.py")
    edges = bridge_endpoints_to_functions("p", [ep], [fn], codegraph_db=cg)
    assert len(edges) == 1 and edges[0].target == fn.id


def test_bridge_uses_controller_file_for_interface_mapping(tmp_path):
    # Spring 接口声明 @PostMapping, @RestController 实现类承载调用链;
    # handler 查找必须从实现类文件起跳, 不能停在接口方法。
    cg = tmp_path / "codegraph.db"
    _make_codegraph(
        cg,
        nodes=[
            ("cg:iface", "queryMappingValue", "api/ParamMappingConfigMicroservice.java", "method"),
            ("cg:impl", "queryMappingValue", "server/ParamMappingConfigMicroserviceImpl.java", "method"),
            ("cg:repo", "selectMappingValue", "server/ParamMappingConfigRepository.java", "method"),
        ],
        edges=[("cg:impl", "cg:repo", "calls")],
    )
    ep = _endpoint(
        "queryMappingValue",
        "api/ParamMappingConfigMicroservice.java",
        {"controller_file": "server/ParamMappingConfigMicroserviceImpl.java"},
    )
    fn = _func("selectMappingValue", "server/ParamMappingConfigRepository.java")

    edges = bridge_endpoints_to_functions("p", [ep], [fn], codegraph_db=cg)

    assert len(edges) == 1
    assert edges[0].source == ep.id
    assert edges[0].target == fn.id
