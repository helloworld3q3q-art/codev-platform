"""综合分析器框架 —— 在确定性血缘骨架上叠加软节点/软边(分析器/LLM 派生)。

定位(A1, 综合理解层): plugins 产确定性硬节点/硬边(血缘骨架);analyzer 在其上归纳更高层
语义(业务域归类等), 产**软节点**(NodeKind.BUSINESS_DOMAIN)+**软边**(BELONGS_TO_DOMAIN)。

软/硬物理隔离(护城河保护): 软产物 confidence<1.0 + 独立 kind + meta.derived_by, impact
默认过滤软边 —— "查依赖"永远走确定性骨架, 不被 LLM 噪声污染。

与 call_resolvers 同构(协议族 + registry 铁律, 零 if-else): 加 analyzer = 写一个 Analyzer
实现 + register_analyzer 一行, 核心(ingest)只依赖本协议。analyzer 是 ingest 的 **second
post-pass**(在 _calls_pass / _frontend_deps_pass 之后, 此时硬骨架已连通可供归纳)。

grounding(抗幻觉, LLM analyzer 必守): LLM 当**标注者**不当发现者 —— 只能从已落库硬节点
归纳, 产出经 referential-integrity 校验(validate_soft_result): 软边端点必须指向真实硬节点,
悬空即丢。确定性校验是硬约束, 不靠 prompt 自觉。
"""
from __future__ import annotations

from dataclasses import replace
from typing import Protocol, runtime_checkable

from codev_platform.graph.schema import (
    AnalyzerResult,
    GraphEdge,
    GraphNode,
    is_soft_edge_kind,
    is_soft_node_kind,
)

# 软产物默认置信度(analyzer 未给或给了非法值 >=1.0 时钳到此, 保证软 < 硬的 1.0)。
_SOFT_DEFAULT_CONF = 0.7


@runtime_checkable
class Analyzer(Protocol):
    """综合分析器契约。各分析维度一个实现, 核心(ingest)只依赖本协议。

    name:    analyzer 唯一标识(如 "business_domain")。
    applies: 廉价判断是否适用(有可归纳的硬节点)。
    analyze: 在硬骨架上归纳, 产软节点 + 软边;失败 fail-soft 返回空 result(不抛)。
    """

    name: str

    def applies(self, nodes: list[GraphNode]) -> bool: ...

    def analyze(self, project_id: str, nodes: list[GraphNode],
                edges: list[GraphEdge]) -> AnalyzerResult: ...


_ANALYZERS: list[Analyzer] = []


def register_analyzer(a: Analyzer) -> None:
    """注册一个 analyzer —— 加分析维度的唯一接入点(在 analyzers/__init__ 调)。"""
    _ANALYZERS.append(a)


def registered_analyzers() -> list[Analyzer]:
    """全部已注册 analyzer(测试 / 审计用)。"""
    return list(_ANALYZERS)


def applicable_analyzers(nodes: list[GraphNode]) -> list[Analyzer]:
    """筛出适用的 analyzer。applies 抛错视为不适用(fail-soft)。"""
    out: list[Analyzer] = []
    for a in _ANALYZERS:
        try:
            if a.applies(nodes):
                out.append(a)
        except Exception:  # noqa: BLE001 — applies 廉价探测, 出错即不适用, 不拖垮 pass
            continue
    return out


def validate_soft_result(result: AnalyzerResult, hard_node_ids: set[str]) -> AnalyzerResult:
    """referential-integrity 校验 + 软标记钳制(确定性硬约束, grounding 的代码侧承重墙)。

    - 软节点(BUSINESS_DOMAIN): meta['confidence'] 缺失/>=1.0 → 钳到默认 <1.0;补 derived_by。
    - 软边(BELONGS_TO_DOMAIN): 两端必须指向真实节点(硬节点 ∪ 本结果软节点), **悬空即丢**;
      confidence>=1.0 → 钳 <1.0。
    - 非软 node/edge 原样保留(analyzer 理论只产软的, 防御性放行, 不校验)。

    返回净化后的**新** result(不改入参)。LLM 越界(产指向虚构节点的软边)在此被确定性拦掉,
    不靠 prompt 自觉。
    """
    soft_node_ids = {n.id for n in result.nodes if is_soft_node_kind(n.kind)}
    valid_ids = hard_node_ids | soft_node_ids

    clean_nodes: list[GraphNode] = []
    for n in result.nodes:
        if is_soft_node_kind(n.kind):
            meta = dict(n.meta)
            conf = meta.get("confidence")
            if not isinstance(conf, (int, float)) or conf >= 1.0:
                meta["confidence"] = _SOFT_DEFAULT_CONF
            meta.setdefault("derived_by", "unknown")
            clean_nodes.append(replace(n, meta=meta))
        else:
            clean_nodes.append(n)

    clean_edges: list[GraphEdge] = []
    for e in result.edges:
        if is_soft_edge_kind(e.kind):
            if e.source not in valid_ids or e.target not in valid_ids:
                continue  # 悬空软边: 端点不存在 → referential-integrity 丢弃
            conf = e.confidence if e.confidence < 1.0 else _SOFT_DEFAULT_CONF
            clean_edges.append(replace(e, confidence=conf))
        else:
            clean_edges.append(e)

    return AnalyzerResult(
        nodes=clean_nodes, edges=clean_edges,
        evidences=result.evidences, findings=result.findings,
        plugin=result.plugin, plugin_version=result.plugin_version,
    )
