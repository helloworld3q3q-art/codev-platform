"""同 id 多插件节点去重 + 软节点视图过滤(统一图谱"看依赖"视图不被角色软节点/重复模块节点污染)。

背景: builtin.vue 的 frontend_component 与 builtin.frontend_deps 的 frontend_module 对同一 .vue
撞同一 id(实测 129 文件双份); arch_layer/business_domain 软节点枢纽状, 在依赖视图里是噪声。
"""
from __future__ import annotations

from codev_platform.graph.schema import (
    GraphNode,
    NodeKind,
    dedup_nodes_by_id,
    is_soft_edge_kind,
    is_soft_node_kind,
)

_PID = "p1"


def _n(nid: str, kind: str, name: str = "x") -> GraphNode:
    return GraphNode(id=nid, kind=kind, name=name, project_id=_PID, file="f.vue")


def test_dedup_keeps_component_over_module_same_id():
    """同 id 的 frontend_module 与 frontend_component → 保留 component(module 让位)。"""
    nid = "p1:frontend_component:src/A.vue"
    # 不论顺序都保留 component
    for order in ([_n(nid, NodeKind.FRONTEND_MODULE.value), _n(nid, NodeKind.FRONTEND_COMPONENT.value)],
                  [_n(nid, NodeKind.FRONTEND_COMPONENT.value), _n(nid, NodeKind.FRONTEND_MODULE.value)]):
        out = dedup_nodes_by_id(order)
        assert len(out) == 1
        assert out[0].kind == NodeKind.FRONTEND_COMPONENT.value


def test_dedup_distinct_ids_untouched():
    out = dedup_nodes_by_id([_n("a", "frontend_component"), _n("b", "frontend_module")])
    assert {n.id for n in out} == {"a", "b"}


def test_dedup_empty():
    assert dedup_nodes_by_id([]) == []


def test_soft_kind_classification_covers_arch_layer_and_domain():
    """arch_layer/business_domain 是软节点, plays_role/belongs_to_domain 是软边(视图默认过滤它们)。"""
    assert is_soft_node_kind(NodeKind.ARCH_LAYER.value)
    assert is_soft_node_kind(NodeKind.BUSINESS_DOMAIN.value)
    assert not is_soft_node_kind(NodeKind.FRONTEND_COMPONENT.value)
    assert is_soft_edge_kind("plays_role")
    assert is_soft_edge_kind("belongs_to_domain")
    assert not is_soft_edge_kind("imports")
