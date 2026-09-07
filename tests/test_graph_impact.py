"""影响分析引擎单测 (graph/impact.py)。

造一个连通小图 前端 --calls_api--> 端点 --calls--> 函数 --reads_table--> 表,
验 4 个查询方向 (impact 反向 / table_usage 反向 / page_deps 正向 / api_callers 反向取前端)。
"""
from __future__ import annotations

import pytest

from codev_platform.graph.impact import (
    find_api_callers,
    find_impact,
    find_page_dependencies,
    find_table_usage,
)
from codev_platform.graph.schema import (
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from codev_platform.graph.store import open_store

_PID = "p"
_FE = "p:frontend_api_call:src/UserPage.tsx:POST:/users"
_EP = "p:backend_endpoint:POST:/users"
_FN = "p:backend_function:repo.py:save_user"
_TB = "p:db_table:users"


@pytest.fixture
def conn(tmp_path):
    c = open_store(_PID, path=tmp_path / "g.sqlite")
    nodes = [
        GraphNode(id=_FE, kind=NodeKind.FRONTEND_API_CALL.value, name="POST /users", project_id=_PID,
                  file="src/UserPage.tsx"),
        GraphNode(id=_EP, kind=NodeKind.BACKEND_ENDPOINT.value, name="create_user", project_id=_PID,
                  file="api.py", meta={"handler": "create_user"}),
        GraphNode(id=_FN, kind=NodeKind.BACKEND_FUNCTION.value, name="save_user", project_id=_PID,
                  file="repo.py"),
        GraphNode(id=_TB, kind=NodeKind.DB_TABLE.value, name="users", project_id=_PID),
    ]
    edges = [
        GraphEdge(source=_FE, target=_EP, kind=EdgeKind.CALLS_API.value),
        GraphEdge(source=_EP, target=_FN, kind=EdgeKind.CALLS.value),
        GraphEdge(source=_FN, target=_TB, kind=EdgeKind.WRITES_TABLE.value),
    ]
    c.upsert_result(_PID, AnalyzerResult(nodes=nodes, edges=edges, plugin="test"))
    return c


def test_find_impact_of_table_reaches_all_upper_layers(conn):
    r = find_impact(conn, _PID, _TB)
    assert r["found"]
    layers = r["impact"]["byLayer"]
    assert {n["id"] for n in layers["backend"]} == {_EP, _FN}
    assert {n["id"] for n in layers["frontend"]} == {_FE}
    assert r["impact"]["total"] == 3  # function + endpoint + frontend


def test_find_table_usage_by_name(conn):
    r = find_table_usage(conn, _PID, "Users")  # 大小写不敏感
    assert r["found"] and r["table"]["id"] == _TB
    ids = {n["id"] for layer in r["usage"]["byLayer"].values() for n in layer}
    assert ids == {_EP, _FN, _FE}


@pytest.fixture
def conn_entity(tmp_path):
    """db_table 节点带 meta.entity_class (ORM 抽取器写入), 验"拿实体类名查影响面"桥。"""
    pid = "e"
    c = open_store(pid, path=tmp_path / "e.sqlite")
    tb = f"{pid}:db_table:oms_inbound_order"
    fn = f"{pid}:backend_function:repo.java:saveInbound"
    nodes = [
        GraphNode(id=tb, kind=NodeKind.DB_TABLE.value, name="OMS_INBOUND_ORDER",
                  project_id=pid, meta={"source": "hibernate-hbm", "entity_class": "OmsInboundOrder"}),
        GraphNode(id=fn, kind=NodeKind.BACKEND_FUNCTION.value, name="saveInbound", project_id=pid,
                  file="repo.java"),
    ]
    edges = [GraphEdge(source=fn, target=tb, kind=EdgeKind.WRITES_TABLE.value)]
    c.upsert_result(pid, AnalyzerResult(nodes=nodes, edges=edges, plugin="test"))
    return c, pid, tb, fn


def test_table_usage_resolves_by_entity_class(conn_entity):
    """拿**实体类名** OmsInboundOrder (非表名) 查 table_usage → 经 meta.entity_class 解析到表。"""
    c, pid, tb, fn = conn_entity
    r = find_table_usage(c, pid, "OmsInboundOrder")
    assert r["found"] and r["table"]["id"] == tb
    ids = {n["id"] for layer in r["usage"]["byLayer"].values() for n in layer}
    assert fn in ids


def test_find_impact_resolves_by_entity_class(conn_entity):
    """find_impact 拿实体类名也能起步 (同一 _resolve 桥, 根治 sample-project-beta found:false)。"""
    c, pid, tb, fn = conn_entity
    r = find_impact(c, pid, "OmsInboundOrder")
    assert r["found"] and r["target"]["id"] == tb
    assert fn in {n["id"] for layer in r["impact"]["byLayer"].values() for n in layer}


def test_real_table_name_still_resolves(conn_entity):
    """桥不破原行为: 真表名 OMS_INBOUND_ORDER 仍直接命中 (entity fallback 只在名字未命中时)。"""
    c, pid, tb, _fn = conn_entity
    assert find_table_usage(c, pid, "oms_inbound_order")["table"]["id"] == tb


def test_find_impacted_pages_transitive(tmp_path):
    # 前端依赖图: page --imports--> barrel --imports--> PermissionButton。
    # 改 PermissionButton, 反向(谁 import 它, 含传递)只取 is_page 模块 = 受影响页面。
    from codev_platform.graph.impact import find_impacted_pages
    pid = "fe"
    c = open_store(pid, path=tmp_path / "fe.sqlite")
    page = f"{pid}:frontend_module:src/pages/foo/index.tsx"
    barrel = f"{pid}:frontend_module:src/components/Button/index.tsx"
    comp = f"{pid}:frontend_module:src/components/Button/PermissionButton.tsx"
    nodes = [
        GraphNode(id=page, kind=NodeKind.FRONTEND_MODULE.value, name="index.tsx",
                  project_id=pid, file="src/pages/foo/index.tsx", meta={"is_page": True}),
        GraphNode(id=barrel, kind=NodeKind.FRONTEND_MODULE.value, name="index.tsx",
                  project_id=pid, file="src/components/Button/index.tsx", meta={"is_page": False}),
        GraphNode(id=comp, kind=NodeKind.FRONTEND_MODULE.value, name="PermissionButton.tsx",
                  project_id=pid, file="src/components/Button/PermissionButton.tsx",
                  meta={"is_page": False}),
    ]
    edges = [
        GraphEdge(source=page, target=barrel, kind=EdgeKind.IMPORTS.value),
        GraphEdge(source=barrel, target=comp, kind=EdgeKind.IMPORTS.value),
    ]
    c.upsert_result(pid, AnalyzerResult(nodes=nodes, edges=edges, plugin="builtin.frontend_deps"))
    r = find_impacted_pages(c, pid, "PermissionButton.tsx")
    assert r["found"]
    assert r["count"] == 1                       # 只 1 个页面(barrel 非 is_page 不计)
    assert all(p["is_page"] for p in r["pages"])  # 返回的全是页面
    assert "pages/foo" in r["pages"][0]["file"]
    c.close()


def test_find_impacted_pages_not_found(conn):
    from codev_platform.graph.impact import find_impacted_pages
    r = find_impacted_pages(conn, _PID, "NoSuchComponent")
    assert r["found"] is False


def test_find_page_dependencies_forward(conn):
    r = find_page_dependencies(conn, _PID, _FE)
    assert r["found"]
    layers = r["dependsOn"]["byLayer"]
    assert {n["id"] for n in layers["backend"]} == {_EP, _FN}
    assert {n["id"] for n in layers["database"]} == {_TB}


def test_find_api_callers_returns_frontend(conn):
    r = find_api_callers(conn, _PID, _EP)
    assert r["found"] and r["count"] == 1
    assert r["callers"][0]["id"] == _FE


def test_unknown_ref_not_found(conn):
    assert find_impact(conn, _PID, "nope")["found"] is False
    assert find_table_usage(conn, _PID, "ghost")["found"] is False


def test_ambiguous_name_returns_candidates(tmp_path):
    # 两个同名端点 list (不同文件) -> 按 name 解析歧义, 回候选; 用 id 才能消歧
    c = open_store("p2", path=tmp_path / "g2.sqlite")
    n1 = GraphNode(id="p2:backend_endpoint:GET:/a/list", kind=NodeKind.BACKEND_ENDPOINT.value,
                   name="list", project_id="p2", file="a.py")
    n2 = GraphNode(id="p2:backend_endpoint:GET:/b/list", kind=NodeKind.BACKEND_ENDPOINT.value,
                   name="list", project_id="p2", file="b.py")
    c.upsert_result("p2", AnalyzerResult(nodes=[n1, n2], plugin="test"))
    r = find_api_callers(c, "p2", "list")
    assert r["found"] is False and len(r["ambiguous"]) == 2
    assert {a["file"] for a in r["ambiguous"]} == {"a.py", "b.py"}
    # 精确 id 仍可解析
    assert find_api_callers(c, "p2", n1.id)["found"] is True


def test_find_api_callers_resolves_by_url_path(tmp_path):
    # 真实形态(sample-project-beta PDA 实证): 端点 id 带 method 前缀、name 是 handler 名, URL 在 meta.url。
    # agent/人 用裸 URL 路径查 → 旧 _resolve(只认 id/name)返 found:false。补 meta.url 路径匹配。
    pid = "u"
    c = open_store(pid, path=tmp_path / "u.sqlite")
    ep = f"{pid}:backend_endpoint:GET:/pda/task/check/container"
    fe = f"{pid}:frontend_api_call:common/js/URL.js:PICK_CHECK_TURN"
    nodes = [
        GraphNode(id=ep, kind=NodeKind.BACKEND_ENDPOINT.value, name="checkContainer",
                  project_id=pid, file="TaskController.java",
                  meta={"url": "/pda/task/check/container", "http_method": "GET"}),
        GraphNode(id=fe, kind=NodeKind.FRONTEND_API_CALL.value, name="PICK_CHECK_TURN",
                  project_id=pid, file="common/js/URL.js",
                  meta={"url": "/pda/task/check/container", "url_registry": True}),
    ]
    edges = [GraphEdge(source=fe, target=ep, kind=EdgeKind.CALLS_API.value)]
    c.upsert_result(pid, AnalyzerResult(nodes=nodes, edges=edges, plugin="test"))
    # 裸 URL 路径(name/id 都不等于它)→ 现在能解析到端点并返回调用方
    r = find_api_callers(c, pid, "/pda/task/check/container")
    assert r["found"] and r["count"] == 1 and r["callers"][0]["id"] == fe
    # 带 method 前缀 / 尾斜杠 也归一命中
    assert find_api_callers(c, pid, "GET /pda/task/check/container")["found"]
    assert find_api_callers(c, pid, "GET:/pda/task/check/container/")["found"]
    # handler name 仍可解析(旧路径不破)
    assert find_api_callers(c, pid, "checkContainer")["found"]
    c.close()


def test_find_api_callers_registry_precise_via_uses_api(tmp_path):
    # url_registry 常量: 共享 URL.js 被两页 import, 但只 scan.vue 真引用了常量(uses_api)。
    # 期望: find_api_callers 只返 scan.vue(精确), 不返 other.vue(只 import 不用)+ 不返 URL.js。
    # 机制: build_impact_graph 丢弃 contains→registry常量 的泛连边, 只走 uses_api 精确边。
    pid = "reg"
    c = open_store(pid, path=tmp_path / "reg.sqlite")
    ep = f"{pid}:backend_endpoint:GET:/pda/task/check/container"
    api = f"{pid}:frontend_api_call:URL.js:PICK_CHECK_TURN"
    urljs = f"{pid}:frontend_component:URL.js"
    scan = f"{pid}:frontend_component:pages/scan.vue"
    other = f"{pid}:frontend_component:pages/other.vue"
    nodes = [
        GraphNode(id=ep, kind=NodeKind.BACKEND_ENDPOINT.value, name="checkContainer", project_id=pid,
                  file="T.java", meta={"url": "/pda/task/check/container", "http_method": "GET"}),
        GraphNode(id=api, kind=NodeKind.FRONTEND_API_CALL.value, name="PICK_CHECK_TURN", project_id=pid,
                  file="URL.js", meta={"url": "/pda/task/check/container", "url_registry": True}),
        GraphNode(id=urljs, kind=NodeKind.FRONTEND_COMPONENT.value, name="URL.js", project_id=pid, file="URL.js"),
        GraphNode(id=scan, kind=NodeKind.FRONTEND_COMPONENT.value, name="scan.vue", project_id=pid, file="pages/scan.vue"),
        GraphNode(id=other, kind=NodeKind.FRONTEND_COMPONENT.value, name="other.vue", project_id=pid, file="pages/other.vue"),
    ]
    edges = [
        GraphEdge(source=api, target=ep, kind=EdgeKind.CALLS_API.value),
        GraphEdge(source=urljs, target=api, kind=EdgeKind.CONTAINS.value),   # 共享注册模块声明常量
        GraphEdge(source=scan, target=urljs, kind=EdgeKind.IMPORTS.value),   # 两页都 import URL.js
        GraphEdge(source=other, target=urljs, kind=EdgeKind.IMPORTS.value),
        GraphEdge(source=scan, target=api, kind=EdgeKind.USES_API.value),    # 但只 scan 真引用常量
    ]
    c.upsert_result(pid, AnalyzerResult(nodes=nodes, edges=edges, plugin="test"))
    r = find_api_callers(c, pid, "/pda/task/check/container")
    assert r["found"]
    ids = {x["id"] for x in r["callers"]}
    assert scan in ids                       # 精确命中真正引用常量的页面
    assert other not in ids                  # 只 import 不引用 → 不算调用方(不再泛连)
    assert urljs not in ids                  # 共享声明模块本身不算调用方
    c.close()


def test_find_api_callers_url_aggregates_methods(tmp_path):
    # 同 URL 路径多 method(GET+POST)→ 按 URL 查应聚合两端点的前端调用方, 不被 method 切碎。
    pid = "um"
    c = open_store(pid, path=tmp_path / "um.sqlite")
    epg = f"{pid}:backend_endpoint:GET:/x/item"
    epp = f"{pid}:backend_endpoint:POST:/x/item"
    fe1 = f"{pid}:frontend_api_call:URL.js:GET_ITEM"
    fe2 = f"{pid}:frontend_api_call:URL.js:SAVE_ITEM"
    nodes = [
        GraphNode(id=epg, kind=NodeKind.BACKEND_ENDPOINT.value, name="getItem", project_id=pid,
                  file="C.java", meta={"url": "/x/item", "http_method": "GET"}),
        GraphNode(id=epp, kind=NodeKind.BACKEND_ENDPOINT.value, name="saveItem", project_id=pid,
                  file="C.java", meta={"url": "/x/item", "http_method": "POST"}),
        GraphNode(id=fe1, kind=NodeKind.FRONTEND_API_CALL.value, name="GET_ITEM", project_id=pid,
                  file="URL.js", meta={"url": "/x/item", "url_registry": True}),
        GraphNode(id=fe2, kind=NodeKind.FRONTEND_API_CALL.value, name="SAVE_ITEM", project_id=pid,
                  file="URL.js", meta={"url": "/x/item", "url_registry": True}),
    ]
    edges = [GraphEdge(source=fe1, target=epg, kind=EdgeKind.CALLS_API.value),
             GraphEdge(source=fe2, target=epp, kind=EdgeKind.CALLS_API.value)]
    c.upsert_result(pid, AnalyzerResult(nodes=nodes, edges=edges, plugin="test"))
    r = find_api_callers(c, pid, "/x/item")
    assert r["found"] and r["count"] == 2
    assert {x["id"] for x in r["callers"]} == {fe1, fe2}
    assert len(r["matchedEndpoints"]) == 2     # 标出聚合了两个 method
    c.close()


def test_find_api_callers_url_matches_path_param_template(tmp_path):
    # 端点存为模板 /users/{id}; 人/agent 从日志/network 敲具体值 /users/123 → 应命中(占位单段通配,
    # 治参数化 REST 路由漏报)。反面: 具体值不该误配字面端点 /users/settings(段不等且都非占位)。
    pid = "pp"
    c = open_store(pid, path=tmp_path / "pp.sqlite")
    tmpl = f"{pid}:backend_endpoint:GET:/users/{{id}}"
    lit = f"{pid}:backend_endpoint:GET:/users/settings"
    fe_t = f"{pid}:frontend_api_call:URL.js:GET_USER"
    fe_l = f"{pid}:frontend_api_call:URL.js:GET_SETTINGS"
    nodes = [
        GraphNode(id=tmpl, kind=NodeKind.BACKEND_ENDPOINT.value, name="getUser", project_id=pid,
                  file="C.java", meta={"url": "/users/{id}", "http_method": "GET"}),
        GraphNode(id=lit, kind=NodeKind.BACKEND_ENDPOINT.value, name="getSettings", project_id=pid,
                  file="C.java", meta={"url": "/users/settings", "http_method": "GET"}),
        GraphNode(id=fe_t, kind=NodeKind.FRONTEND_API_CALL.value, name="GET_USER", project_id=pid, file="URL.js"),
        GraphNode(id=fe_l, kind=NodeKind.FRONTEND_API_CALL.value, name="GET_SETTINGS", project_id=pid, file="URL.js"),
    ]
    edges = [GraphEdge(source=fe_t, target=tmpl, kind=EdgeKind.CALLS_API.value),
             GraphEdge(source=fe_l, target=lit, kind=EdgeKind.CALLS_API.value)]
    c.upsert_result(pid, AnalyzerResult(nodes=nodes, edges=edges, plugin="test"))
    # 具体值命中模板(治漏报)且只命中模板, 不误配字面 /users/settings(不过报)
    r = find_api_callers(c, pid, "/users/123")
    assert r["found"] and {x["id"] for x in r["callers"]} == {fe_t}
    # 段数不等 → 不命中(占位只通配单段)
    assert not find_api_callers(c, pid, "/users/123/roles")["found"]
    c.close()


def test_search_nodes_multiword_ranks_by_term_hits(conn):
    # 多词 query: 整串"save user"匹配不到任何 name(旧行为 0 命中); 分词后 save_user 命中
    # save+user 两词排第一, 修复 graph lane 多词检索弱点(直接提升融合召回质量)。
    from codev_platform.graph.impact import search_nodes
    r = search_nodes(conn, _PID, "save user")
    assert r["count"] >= 1
    assert r["hits"][0]["name"] == "save_user"   # 命中 2 词, 排第一


def test_search_nodes_single_word_unchanged(conn):
    # 单词行为不变: 所有 name 含该词的节点都入选。
    from codev_platform.graph.impact import search_nodes
    names = {h["name"] for h in search_nodes(conn, _PID, "user")["hits"]}
    assert {"save_user", "create_user", "users"} <= names


def test_name_relevance_tiers_exact_prefix_substring():
    from codev_platform.graph.impact_soft import _name_relevance  # 2026-06-13 软查询分出
    # 越相关键越小: 精确 < 前缀 < 子串(同一词 save)
    exact = _name_relevance("save", ["save"], "save")
    prefix = _name_relevance("save_user", ["save"], "save")
    substr = _name_relevance("unsaved", ["save"], "save")
    assert exact < prefix < substr
    assert _name_relevance("foo", ["bar"], "bar") is None        # 0 命中 → None
    # 同档短名优先
    assert _name_relevance("ab_save", ["save"], "save") < _name_relevance("ab_save_xyz", ["save"], "save")
    # 多词: 命中词多优先
    assert _name_relevance("save_user", ["save", "user"], "save user") < \
        _name_relevance("save_x", ["save", "user"], "save user")


def test_search_nodes_ranks_exact_prefix_substring(tmp_path):
    from codev_platform.graph.impact import search_nodes
    c = open_store("p3", path=tmp_path / "g3.sqlite")
    nodes = [
        GraphNode(id="p3:backend_function:save", kind=NodeKind.BACKEND_FUNCTION.value,
                  name="save", project_id="p3", file="a.py"),               # 精确
        GraphNode(id="p3:backend_function:save_user", kind=NodeKind.BACKEND_FUNCTION.value,
                  name="save_user", project_id="p3", file="b.py"),          # 前缀
        GraphNode(id="p3:backend_function:unsaved", kind=NodeKind.BACKEND_FUNCTION.value,
                  name="unsaved", project_id="p3", file="c.py"),            # 子串
    ]
    c.upsert_result("p3", AnalyzerResult(nodes=nodes, plugin="test"))
    names = [h["name"] for h in search_nodes(c, "p3", "save")["hits"]]
    c.close()
    assert names == ["save", "save_user", "unsaved"]   # 精确 > 前缀 > 子串


def test_resolve_duplicate_edges_keeps_stamped(tmp_path):
    # 冲突消解: 同 (s,t,kind) 一个盖戳一个没盖 → 保盖戳者(provenance 更全)。
    from codev_platform.graph.edge_resolve import resolve_duplicate_edges
    from codev_platform.graph.schema import ProvSource, edge_provenance, stamp_provenance
    stamped = stamp_provenance(GraphEdge(source="a", target="b", kind="calls", confidence=0.7),
                               ProvSource.AST)
    unstamped = GraphEdge(source="a", target="b", kind="calls", confidence=0.7)
    out = resolve_duplicate_edges([unstamped, stamped])
    assert len(out) == 1 and edge_provenance(out[0].meta)["src"] == "ast"


def test_resolve_duplicate_edges_higher_confidence_wins():
    from codev_platform.graph.edge_resolve import resolve_duplicate_edges
    e1 = GraphEdge(source="a", target="b", kind="calls", confidence=0.5)
    e2 = GraphEdge(source="a", target="b", kind="calls", confidence=0.9)
    out = resolve_duplicate_edges([e1, e2])
    assert len(out) == 1 and out[0].confidence == 0.9


def test_resolve_duplicate_edges_distinct_targets_untouched():
    from codev_platform.graph.edge_resolve import resolve_duplicate_edges
    edges = [GraphEdge(source="a", target="b", kind="calls"),
             GraphEdge(source="a", target="c", kind="calls")]   # 不同 target 非冲突
    assert resolve_duplicate_edges(edges) == edges


def test_find_impact_paths_ranks_by_edge_quality(tmp_path):
    # Phase 5: 两条入边到表 t —— B 经 ast/1.0, C 经 regex/0.65 → B 路径分更高排前。
    from codev_platform.graph.impact import find_impact_paths
    from codev_platform.graph.schema import ProvSource, stamp_provenance
    c = open_store("pp", path=tmp_path / "pp.sqlite")
    TB, B, C = "pp:db_table:t", "pp:backend_function:b", "pp:backend_function:c"
    nodes = [
        GraphNode(id=TB, kind=NodeKind.DB_TABLE.value, name="t", project_id="pp"),
        GraphNode(id=B, kind=NodeKind.BACKEND_FUNCTION.value, name="bfn", project_id="pp", file="b.py"),
        GraphNode(id=C, kind=NodeKind.BACKEND_FUNCTION.value, name="cfn", project_id="pp", file="c.py"),
    ]
    e_ast = stamp_provenance(
        GraphEdge(source=B, target=TB, kind=EdgeKind.READS_TABLE.value, confidence=1.0), ProvSource.AST)
    e_rgx = stamp_provenance(
        GraphEdge(source=C, target=TB, kind=EdgeKind.READS_TABLE.value, confidence=0.65), ProvSource.REGEX)
    c.upsert_result("pp", AnalyzerResult(nodes=nodes, edges=[e_ast, e_rgx], plugin="test"))
    r = find_impact_paths(c, "pp", "t")
    c.close()
    assert r["found"] and r["count"] == 2
    assert r["paths"][0]["endpoint"]["id"] == B                  # ast/1.0 路径最强
    assert r["paths"][0]["score"] > r["paths"][1]["score"]
    assert r["paths"][0]["certain"] is True and r["paths"][1]["certain"] is False
    hop = r["paths"][0]["hops"][0]                                # 每跳有证据
    assert hop["via_edge"] == "reads_table" and hop["src"] == "ast" and hop["confidence"] == 1.0


def test_find_impact_paths_top_n_and_depth(conn):
    from codev_platform.graph.impact import find_impact_paths
    r = find_impact_paths(conn, _PID, _TB, top_n=2)
    assert r["count"] == 2 and r["totalReached"] == 3            # FN/EP/FE 可达, 取 top 2
    assert r["paths"][0]["endpoint"]["id"] == _FN                 # 最浅(d1)分最高
    assert r["paths"][0]["depth"] == 1
    assert r["paths"][0]["score"] >= r["paths"][1]["score"]      # 降序稳定


def test_find_impact_paths_kind_weight_and_depth_decay(tmp_path):
    # Phase 5: ① kind 权重(calls 强依赖 > imports 弱依赖, 同 src/conf/depth)② 深度衰减(单跳满质量边
    # 的 score = 1.0 × _DEPTH_DECAY, 而非 1.0)。两者正交叠加, 一个用例同时验。
    from codev_platform.graph.impact import _DEPTH_DECAY, find_impact_paths
    from codev_platform.graph.schema import ProvSource, stamp_provenance
    c = open_store("kw", path=tmp_path / "kw.sqlite")
    T, A, B = "kw:db_table:t", "kw:backend_function:a", "kw:backend_function:b"
    nodes = [
        GraphNode(id=T, kind=NodeKind.DB_TABLE.value, name="t", project_id="kw"),
        GraphNode(id=A, kind=NodeKind.BACKEND_FUNCTION.value, name="afn", project_id="kw", file="a.py"),
        GraphNode(id=B, kind=NodeKind.BACKEND_FUNCTION.value, name="bfn", project_id="kw", file="b.py"),
    ]
    # 同 ast/conf 1.0、同 depth 1; A 经 calls(强关系 1.0), B 经 imports(弱关系 0.85)。
    e_a = stamp_provenance(
        GraphEdge(source=A, target=T, kind=EdgeKind.CALLS.value, confidence=1.0), ProvSource.AST)
    e_b = stamp_provenance(
        GraphEdge(source=B, target=T, kind=EdgeKind.IMPORTS.value, confidence=1.0), ProvSource.AST)
    c.upsert_result("kw", AnalyzerResult(nodes=nodes, edges=[e_a, e_b], plugin="test"))
    r = find_impact_paths(c, "kw", "t")
    c.close()
    assert r["paths"][0]["endpoint"]["id"] == A                  # calls(1.0) 排在 imports(0.85) 前
    assert abs(r["paths"][0]["score"] - round(_DEPTH_DECAY, 4)) < 1e-9   # 深度衰减已计入(0.9, 非 1.0)
    assert r["paths"][0]["score"] > r["paths"][1]["score"]       # kind 权重拉开差距
