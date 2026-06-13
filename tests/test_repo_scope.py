"""RepoScope —— 多根 ingest 仓命名空间(写侧 localize / 读侧 resolve)单测。

纯逻辑(无 IO): 主仓 tag='' 不动既有 id; extra 仓前端节点 id/file 打 tag 仓内唯一(后端端点不动),
边端点同步改写; resolve 还原 tagged file → (来源仓, 真实相对路径)。
"""
from __future__ import annotations

from pathlib import Path

from codev_platform.graph.repo_scope import RepoScope
from codev_platform.graph.schema import (
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)


def _fe(nid: str, file: str) -> GraphNode:
    return GraphNode(id=nid, kind=NodeKind.FRONTEND_MODULE.value, name="x", project_id="p", file=file)


def test_single_repo_is_noop_and_passthrough(tmp_path):
    main = tmp_path / "main"
    scope = RepoScope([main])
    ar = AnalyzerResult(nodes=[_fe("p:frontend_module:src/x.vue", "src/x.vue")])
    scope.localize(ar, main)
    assert ar.nodes[0].id == "p:frontend_module:src/x.vue"   # 单仓: id 一字不变
    assert ar.nodes[0].file == "src/x.vue"
    assert scope.resolve("src/x.vue") == (None, "src/x.vue")  # 无 tag → 透传


def test_main_repo_not_tagged_extra_repo_tagged(tmp_path):
    main, pda = tmp_path / "web", tmp_path / "pda"
    scope = RepoScope([main, pda])
    fe_main = _fe("p:frontend_module:src/index.vue", "src/index.vue")
    scope.localize(AnalyzerResult(nodes=[fe_main]), main)
    assert fe_main.id == "p:frontend_module:src/index.vue"   # 主仓 tag='' → 不动

    fe_pda = _fe("p:frontend_module:src/index.vue", "src/index.vue")
    scope.localize(AnalyzerResult(nodes=[fe_pda]), pda)
    assert fe_pda.id == "pda::p:frontend_module:src/index.vue"   # extra 仓打 tag → 与主仓不再撞
    assert fe_pda.file == "pda::src/index.vue"
    assert fe_pda.meta["repo_root"] == str(pda)


def test_localize_tags_frontend_not_backend_and_rewrites_edges(tmp_path):
    main, pda = tmp_path / "web", tmp_path / "pda"
    scope = RepoScope([main, pda])
    fe = _fe("p:frontend_module:src/index.vue", "src/index.vue")
    api = GraphNode(id="p:frontend_api_call:src/index.vue:GET_X", kind=NodeKind.FRONTEND_API_CALL.value,
                    name="GET_X", project_id="p", file="src/index.vue")
    be = GraphNode(id="p:backend_endpoint:GET:/x", kind=NodeKind.BACKEND_ENDPOINT.value,
                   name="getX", project_id="p", file="C.java")
    edges = [GraphEdge(source=fe.id, target=api.id, kind=EdgeKind.CONTAINS.value),
             GraphEdge(source=api.id, target=be.id, kind=EdgeKind.CALLS_API.value)]
    scope.localize(AnalyzerResult(nodes=[fe, api, be], edges=edges), pda)
    assert fe.id == "pda::p:frontend_module:src/index.vue"
    assert api.id == "pda::p:frontend_api_call:src/index.vue:GET_X"
    assert be.id == "p:backend_endpoint:GET:/x"          # 后端端点(按 URL 契约)不打 tag
    # 前端→前端边: 两端都改写; 前端→后端边: 前端端改写, 后端端保持(_link 仍按 URL 连得上)
    assert edges[0].source == fe.id and edges[0].target == api.id
    assert edges[1].source == api.id and edges[1].target == be.id


def test_resolve_roundtrip_and_unknown_tag(tmp_path):
    main, pda = tmp_path / "web", tmp_path / "pda"
    scope = RepoScope([main, pda])
    assert scope.resolve("pda::src/index.vue") == (pda, "src/index.vue")  # tagged → 来源仓 + 真实路径
    assert scope.resolve("src/index.vue") == (None, "src/index.vue")      # 无 tag → 透传(单仓/主仓)
    assert scope.resolve("ghost::a.vue") == (None, "ghost::a.vue")        # 未知 tag → 透传(回退遍历全仓)


def test_duplicate_basename_extras_get_unique_tags(tmp_path):
    # 两 extra 仓同 basename 'app' → tag 仓间唯一(第二个缀序号), resolve 各自定位到对的仓。
    a, b = tmp_path / "x" / "app", tmp_path / "y" / "app"
    scope = RepoScope([tmp_path / "main", a, b])
    assert scope.resolve("app::z")[0] == a
    assert scope.resolve("app-2::z")[0] == b
