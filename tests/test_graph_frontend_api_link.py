"""A3 前端→后端 API 链接 analyzer 测试(FakeApiLinkLabeler, 零网络, 全确定性)。

覆盖: ① 盲前端文件被识别 + 产 inferred_api_call 软节点 + calls_api_inferred 软边(grounding 到
真实 endpoint);② 已被静态 calls_api 连上的前端文件跳过(不重复);③ grounding reject(labeler
回越界 ep ref → 剔除, 不产悬空软边);④ 无 repo → no-op;⑤ 软产物经 base.validate 软隔离;
⑥ BrainApiLinkLabeler 解析层越界 ep ref 剔除(mock provider, 不真打 LLM)。
"""
from __future__ import annotations

import pytest

from codev_platform.graph.analyzers.api_link_labeler import (
    ApiLinkLabel,
    ApiLinkRequest,
    FakeApiLinkLabeler,
)
from codev_platform.graph.analyzers.base import validate_soft_result
from codev_platform.graph.analyzers.frontend_api_link import FrontendApiLinkAnalyzer
from codev_platform.graph.schema import (
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
    is_soft_edge_kind,
    is_soft_node_kind,
)

PID = "p"


def _endpoint(url, method="POST", name=None):
    return GraphNode(
        id=f"{PID}:backend_endpoint:{method}:{url}", kind=NodeKind.BACKEND_ENDPOINT,
        name=name or f"{method} {url}", project_id=PID,
        meta={"url": url, "http_method": method})


def _frontend_component(rel, name="Foo"):
    return GraphNode(id=f"{PID}:frontend_component:{rel}", kind=NodeKind.FRONTEND_COMPONENT,
                     name=name, project_id=PID, file=rel, language="vue")


def _write(repo, rel, text):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# ---- ① 盲前端文件 → 产软节点 + 软边(grounding 到真实 endpoint) ----

def test_blind_frontend_file_gets_inferred_call(tmp_path):
    # thorn6 风格: 业务封装内联 request, url 在 snippet 里但静态层没建 calls_api 边。
    _write(tmp_path, "src/views/Currency.vue",
           "<script>\nfunction load() {\n  return request({ url: '/pda/currency/list', "
           "method: 'post' })\n}\n</script>")
    nodes = [
        _frontend_component("src/views/Currency.vue", "Currency"),
        _endpoint("/pda/currency/list"),
        _endpoint("/pda/other/thing"),
    ]
    a = FrontendApiLinkAnalyzer(FakeApiLinkLabeler(), repo_path=tmp_path)
    assert a.applies(nodes) is True
    res = a.analyze(PID, nodes, [])
    # FakeLabeler 按 url 末段 "list" 命中 → 连到 /pda/currency/list, 不连 other/thing。
    soft = [n for n in res.nodes if n.kind == NodeKind.INFERRED_API_CALL.value]
    assert len(soft) == 1
    edges = [e for e in res.edges if e.kind == EdgeKind.CALLS_API_INFERRED.value]
    assert len(edges) == 1
    assert edges[0].target == f"{PID}:backend_endpoint:POST:/pda/currency/list"
    assert edges[0].confidence < 1.0
    assert soft[0].meta["confidence"] < 1.0  # 软隔离


# ---- ② 已被静态 calls_api 连上的文件 → 跳过 ----

def test_statically_linked_file_skipped(tmp_path):
    _write(tmp_path, "src/views/Currency.vue", "request({ url: '/pda/currency/list' })")
    fc = _frontend_component("src/views/Currency.vue")
    api_node = GraphNode(id=f"{PID}:frontend_api_call:x", kind=NodeKind.FRONTEND_API_CALL,
                         name="x", project_id=PID, file="src/views/Currency.vue",
                         meta={"url": "/pda/currency/list"})
    ep = _endpoint("/pda/currency/list")
    # 静态层已建该文件节点 → endpoint 的 calls_api 硬边。
    static_edge = GraphEdge(source=api_node.id, target=ep.id, kind=EdgeKind.CALLS_API)
    a = FrontendApiLinkAnalyzer(FakeApiLinkLabeler(), repo_path=tmp_path)
    res = a.analyze(PID, [fc, api_node, ep], [static_edge])
    assert res.nodes == [] and res.edges == []  # 文件已连 → A3 不重复处理


# ---- ③ grounding reject: labeler 回越界 ep ref → 不产悬空软边 ----

class _HallucLabeler:
    """labeler 越界引一个不在候选清单的 ep ref(模拟幻觉造端点)。"""

    signature = "halluc-v1"

    def label(self, batch):
        return [ApiLinkLabel(req.file.path, ("ep999",)) for req in batch]


def test_grounding_rejects_out_of_range_ref(tmp_path):
    _write(tmp_path, "src/A.vue", "request({ url: '/x' })")
    nodes = [_frontend_component("src/A.vue"), _endpoint("/pda/currency/list")]
    a = FrontendApiLinkAnalyzer(_HallucLabeler(), repo_path=tmp_path)
    res = a.analyze(PID, nodes, [])
    # ep999 不在候选(只有 ep1)→ 解析时剔除, 无软节点/软边。
    assert res.nodes == [] and res.edges == []


# ---- ④ 无 repo → no-op(A3 必须读源码, 拿不到不瞎猜) ----

def test_no_repo_is_noop():
    nodes = [_frontend_component("src/A.vue"), _endpoint("/x")]
    a = FrontendApiLinkAnalyzer(FakeApiLinkLabeler(), repo_path=None)
    assert a.applies(nodes) is False


def test_no_backend_or_no_frontend_is_noop(tmp_path):
    a = FrontendApiLinkAnalyzer(FakeApiLinkLabeler(), repo_path=tmp_path)
    assert a.applies([_endpoint("/x")]) is False                  # 无前端
    assert a.applies([_frontend_component("src/A.vue")]) is False  # 无后端


# ---- ⑤ 软产物经 base.validate 软隔离 + 悬空软边丢弃 ----

def test_validate_keeps_grounded_inferred_edge(tmp_path):
    _write(tmp_path, "src/Currency.vue", "request({ url: '/pda/currency/list' })")
    ep = _endpoint("/pda/currency/list")
    nodes = [_frontend_component("src/Currency.vue"), ep]
    a = FrontendApiLinkAnalyzer(FakeApiLinkLabeler(), repo_path=tmp_path)
    raw = a.analyze(PID, nodes, [])
    hard_ids = {n.id for n in nodes}
    clean = validate_soft_result(raw, hard_ids)
    assert is_soft_node_kind(clean.nodes[0].kind)
    assert is_soft_edge_kind(clean.edges[0].kind)
    assert clean.edges[0].target == ep.id      # grounding 到真实 endpoint, 不被悬空丢


def test_validate_drops_dangling_inferred_edge():
    # 软边 target 指向不存在的 endpoint → referential-integrity 丢弃。
    call = GraphNode(id=f"{PID}:inferred_api_call:a->ghost", kind=NodeKind.INFERRED_API_CALL,
                     name="a", project_id=PID)
    edge = GraphEdge(source=call.id, target=f"{PID}:backend_endpoint:GHOST",
                     kind=EdgeKind.CALLS_API_INFERRED, confidence=0.6)
    from codev_platform.graph.schema import AnalyzerResult
    out = validate_soft_result(AnalyzerResult(nodes=[call], edges=[edge]), set())
    assert out.edges == []                      # target 悬空 → 丢


# ---- ⑥ BrainApiLinkLabeler 解析层: 越界 ep ref 剔除(mock provider) ----

class _MockTurn:
    def __init__(self, text):
        self.text = text


class _MockProvider:
    model = "mock-model"

    def __init__(self, text):
        self._text = text

    def chat(self, system, messages, tools):
        return _MockTurn(self._text)


def test_brain_labeler_drops_out_of_range_ref():
    from codev_platform.graph.analyzers.api_link_labeler import EndpointRef, FrontendFile
    from codev_platform.graph.analyzers.brain_api_link_labeler import BrainApiLinkLabeler

    req = ApiLinkRequest(
        file=FrontendFile(path="src/A.vue", snippet="request({url:'/x'})"),
        endpoints=(EndpointRef(ref="ep1", url="/x", method="POST", name="POST /x"),))
    # LLM 回一个合法 ep1 + 一个越界 ep9 → 只保 ep1。
    prov = _MockProvider('[{"file":"f1","endpoints":["ep1","ep9"]}]')
    labeler = BrainApiLinkLabeler(provider=prov)
    out = labeler.label([req])
    assert len(out) == 1
    assert out[0].endpoint_refs == ("ep1",)


def test_brain_labeler_unavailable_is_failsoft():
    from codev_platform.graph.analyzers.api_link_labeler import FrontendFile
    from codev_platform.graph.analyzers.brain_api_link_labeler import BrainApiLinkLabeler

    req = ApiLinkRequest(file=FrontendFile(path="src/A.vue", snippet="x"), endpoints=())
    labeler = BrainApiLinkLabeler(cfg={})   # 无 provider 解析 → None
    # available 取决于环境; 直接验 label 在无 provider 时 fail-soft 返空 refs。
    if not labeler.available():
        out = labeler.label([req])
        assert out == [ApiLinkLabel("src/A.vue")]


def test_analyzer_registered_with_config_gate():
    """config gate: enabled=true 才注册(对称 A1/A2)。"""
    from codev_platform.graph.analyzers import _register_configured, base

    saved = list(base._ANALYZERS)
    base._ANALYZERS.clear()
    try:
        n = _register_configured({"analyzers": {"frontend_api_link": {"enabled": True}}})
        names = [a.name for a in base._ANALYZERS]
        assert "frontend_api_link" in names and n >= 1
    finally:
        base._ANALYZERS.clear()
        base._ANALYZERS.extend(saved)
