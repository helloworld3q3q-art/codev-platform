"""ArchLayerAnalyzer(A2-1 确定性骨架)—— FakeLayerLabeler 管线 + 软硬隔离 + closed-world 越界剔除。

A2-1 全确定性(不调 LLM): 验证框架跑通 + 复用 A1 护栏(软节点/边产出、隔离 kind、越界 ref/layer
剔除、validate 钳制/丢悬空、fail-soft)。真准确率验收靠 A2-3 BrainLayerLabeler。
"""
from __future__ import annotations

from codev_platform.graph.analyzers.architecture_layer import ArchLayerAnalyzer
from codev_platform.graph.analyzers.base import validate_soft_result
from codev_platform.graph.analyzers.layer_labeler import (
    FakeLayerLabeler,
    LayerLabel,
)
from codev_platform.graph.schema import (
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
    is_soft_edge_kind,
    is_soft_node_kind,
)


def _file(path):
    return GraphNode(id=f"p:file:{path}", kind=NodeKind.FILE, name=path, project_id="p", file=path)


def _endpoint(path, name):
    return GraphNode(id=f"p:backend_endpoint:{name}", kind=NodeKind.BACKEND_ENDPOINT,
                     name=name, project_id="p", file=path)


def _func(path, name):
    return GraphNode(id=f"p:backend_function:{name}", kind=NodeKind.BACKEND_FUNCTION,
                     name=name, project_id="p", file=path)


def _table(name):
    return GraphNode(id=f"p:db_table:{name}", kind=NodeKind.DB_TABLE, name=name, project_id="p")


def test_applies_needs_file_nodes():
    a = ArchLayerAnalyzer(FakeLayerLabeler())
    assert a.applies([_file("x.py")]) is True
    assert a.applies([_table("orders")]) is False   # 无 FILE 节点 → no-op


def test_analyze_produces_soft_layer_nodes_and_edges():
    a = ArchLayerAnalyzer(FakeLayerLabeler())
    nodes = [
        _file("web/routes/orders.py"), _endpoint("web/routes/orders.py", "GET /orders"),
        _file("web/repositories/order_repo.py"), _func("web/repositories/order_repo.py", "save"),
        _table("orders"),
    ]
    edges = [GraphEdge(source="p:backend_function:save", target="p:db_table:orders",
                       kind=EdgeKind.READS_TABLE)]
    res = a.analyze("p", nodes, edges)
    # 软节点全是 ARCH_LAYER
    assert res.nodes and all(n.kind == NodeKind.ARCH_LAYER.value for n in res.nodes)
    assert {n.name for n in res.nodes} >= {"controller", "repository"}
    # 软边全是 PLAYS_ROLE; orders.py(有 endpoint)→ controller, order_repo.py(reads table)→ repository
    layer_of = {}
    for e in res.edges:
        assert e.kind == EdgeKind.PLAYS_ROLE.value
        layer_of[e.source] = e.target
    assert layer_of["p:file:web/routes/orders.py"].endswith("controller")
    assert layer_of["p:file:web/repositories/order_repo.py"].endswith("repository")


def test_soft_isolation_kinds():
    # ARCH_LAYER / PLAYS_ROLE 被 schema 认作软(impact 默认过滤 + referential-integrity 的依据)。
    assert is_soft_node_kind(NodeKind.ARCH_LAYER.value)
    assert is_soft_edge_kind(EdgeKind.PLAYS_ROLE.value)


def test_validate_soft_clamps_conf_and_no_dangling():
    # 复用 A1 base.validate_soft_result: ARCH_LAYER conf 钳<1.0 + 悬空 PLAYS_ROLE 软边丢。
    a = ArchLayerAnalyzer(FakeLayerLabeler())
    nodes = [_file("services/x.py")]
    res = a.analyze("p", nodes, [])
    clean = validate_soft_result(res, {n.id for n in nodes})
    assert clean.nodes
    for n in clean.nodes:
        assert n.meta["confidence"] < 1.0
    soft_ids = {n.id for n in clean.nodes}
    for e in clean.edges:
        assert e.source in {n.id for n in nodes} and e.target in soft_ids   # 不悬空


def test_closed_world_rejects_out_of_vocab_role_and_ref():
    # labeler 返回越界 layer / 越界 ref → 回填时剔除(grounding 硬约束 > prompt)。
    class _BadLabeler:
        def label(self, batch):
            return [LayerLabel(req.batch_id, (
                (req.files[0].ref, "wizard"),   # 越界 layer(枚举外)
                ("f999", "service"),            # 越界 ref(造的假)
            )) for req in batch]

    res = ArchLayerAnalyzer(_BadLabeler()).analyze("p", [_file("a/x.py")], [])
    assert res.nodes == [] and res.edges == []   # 两个越界都被剔, 零软产物


def test_labeler_failure_fail_soft():
    class _BoomLabeler:
        def label(self, batch):
            raise RuntimeError("boom")

    res = ArchLayerAnalyzer(_BoomLabeler()).analyze("p", [_file("a/x.py")], [])
    assert res.nodes == [] and res.edges == []   # fail-soft, 不抛


def test_batch_by_directory_o_dirs_not_files():
    # 同目录多 file 进同一 batch(O(目录数) 成本); 不同目录分开。
    a = ArchLayerAnalyzer(FakeLayerLabeler())
    facts = a._build_facts([_file("svc/a.py"), _file("svc/b.py"), _file("repo/c.py")], [])
    batches, _ = a._batch_by_dir(facts)
    assert {b.batch_id for b in batches} == {"svc", "repo"}
    svc = next(b for b in batches if b.batch_id == "svc")
    assert len(svc.files) == 2   # svc/ 两个 file 一批


# ---- A2-2: BrainLayerLabeler(mock provider) + 缓存 + config gate 注册 ----

def test_brain_labeler_parses_and_double_rejects():
    from codev_platform.graph.analyzers.brain_layer_labeler import BrainLayerLabeler
    from codev_platform.graph.analyzers.layer_labeler import FileFact, LayerRequest

    class _Turn:
        text = ('[{"batch":"b1","roles":['
                '{"file":"f1","layer":"controller"},'
                '{"file":"f2","layer":"wizard"},'    # 越界 layer(枚举外)
                '{"file":"f9","layer":"service"}]}]')  # 越界 ref(造的假)

    class _Prov:
        model = "fake-model"

        def chat(self, system, messages, tools):
            return _Turn()

    lab = BrainLayerLabeler(provider=_Prov())
    req = LayerRequest("dir1", (FileFact("f1", "a.py"), FileFact("f2", "b.py")))
    [out] = lab.label([req])
    assert out.roles == (("f1", "controller"),)   # 越界 layer + 越界 ref 双剔
    assert "fake-model" in lab.signature          # signature 含 model(缓存键维度)


def test_brain_labeler_chat_failure_fail_soft():
    from codev_platform.graph.analyzers.brain_layer_labeler import BrainLayerLabeler
    from codev_platform.graph.analyzers.layer_labeler import FileFact, LayerRequest

    class _Prov:
        model = "m"

        def chat(self, *a, **k):
            raise RuntimeError("boom")

    out = BrainLayerLabeler(provider=_Prov()).label(
        [LayerRequest("d", (FileFact("f1", "x.py"),))])
    assert out[0].roles == ()   # chat 抛 → fail-soft 空(不拖垮 ingest)


def test_cache_second_run_skips_labeler(tmp_path):
    calls = {"n": 0}

    class _Counting(FakeLayerLabeler):
        def label(self, batch):
            calls["n"] += 1
            return super().label(batch)

    a = ArchLayerAnalyzer(_Counting(), cache_dir=tmp_path)
    nodes = [_file("svc/x.py")]
    a.analyze("p", nodes, [])
    assert calls["n"] == 1
    a.analyze("p", nodes, [])   # 同事实 → 命中 per-batch fingerprint 缓存
    assert calls["n"] == 1      # labeler 未再调


def test_register_arch_layer_config_gate():
    import codev_platform.graph.analyzers as A
    from codev_platform.graph.analyzers.base import _ANALYZERS
    saved = list(_ANALYZERS)
    try:
        _ANALYZERS.clear()
        A._register_configured({"analyzers": {"arch_layer": {"enabled": True}}})
        assert "arch_layer" in [a.name for a in A.registered_analyzers()]
        _ANALYZERS.clear()
        assert A._register_configured({}) == 0   # 默认关
    finally:
        _ANALYZERS.clear()
        _ANALYZERS.extend(saved)
