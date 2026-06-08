"""架构分层映射 analyzer(A2)—— 在硬骨架上给 file 归架构层角色软节点(对称 business_domain)。

实现 A1 的 Analyzer 协议。LLM 全隔离在 LayerLabeler 接口后(A2-1 用 FakeLayerLabeler 全确定性
测; A2-2 接 BrainLayerLabeler)。本模块只做**确定性**部分: 建 FileFact 事实包 / 按目录聚类 /
解析回填。referential-integrity(悬空软边丢)+ confidence 钳制由 base.validate_soft_result 兜底。

聚类: 按**目录/包**分批(开发者按层组织目录: services/ repositories/ 是强先验, 同 A1 用 file
作业务域先验)。一个 batch 喂一次 labeler, O(目录数) 非 O(文件数)。

grounding(closed-world): file 编号成 ref(f1/f2)+ 确定性事实(endpoint/表/import/符号)喂 labeler,
labeler 只能 ① 引清单内 ref ② 选 LAYER_ROLES 枚举 layer —— 越界 ref / 越界 layer 解析时剔除。

A2-1 范围: 框架 + 确定性管线 + FakeLayerLabeler(可不调 LLM 跑通)。缓存 / ownership override /
违规检测留 A2-2 / A2-3(复用 business_domain 同款实现)。
"""
from __future__ import annotations

import logging

from codev_platform.graph.analyzers.layer_labeler import (
    LAYER_ROLES,
    FileFact,
    LayerLabel,
    LayerLabeler,
    LayerRequest,
)
from codev_platform.graph.schema import (
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)

logger = logging.getLogger(__name__)

_CONF = 0.7                   # 软产物置信(< 1.0; validate_soft_result 也兜底钳制)
_MAX_BATCHES = 500           # 目录数上限(成本护栏)
_MAX_FILES_PER_BATCH = 60    # 单目录喂 labeler 的 file 上限(防爆 token)


class ArchLayerAnalyzer:
    """架构分层 analyzer。LLM 经 LayerLabeler 注入(不直接依赖 brain)。"""

    name = "arch_layer"

    def __init__(self, labeler: LayerLabeler, *, max_batches: int = _MAX_BATCHES,
                 max_files_per_batch: int = _MAX_FILES_PER_BATCH) -> None:
        self._labeler = labeler
        self._max_batches = max_batches
        self._max_files = max_files_per_batch

    # ---- Analyzer 协议 ----

    def applies(self, nodes: list[GraphNode]) -> bool:
        if not any(n.kind == NodeKind.FILE.value for n in nodes):
            return False
        avail = getattr(self._labeler, "available", None)
        return avail() if callable(avail) else True

    def analyze(self, project_id: str, nodes: list[GraphNode],
                edges: list[GraphEdge]) -> AnalyzerResult:
        facts = self._build_facts(nodes, edges)        # file_id → (path, fact dict)
        if not facts:
            return AnalyzerResult(plugin=self.name)
        batches, ref_maps = self._batch_by_dir(facts)
        if len(batches) > self._max_batches:           # 成本护栏: 超量截断(fail-soft)
            logger.warning("[arch_layer] %d batches > cap %d, truncating",
                           len(batches), self._max_batches)
            batches = batches[: self._max_batches]
        labels = self._safe_label(batches)

        soft_nodes: dict[str, GraphNode] = {}          # 按 layer_id 去重(同角色软节点复用)
        soft_edges: list[GraphEdge] = []
        for lab in labels:
            ns, es = self._to_soft(project_id, lab, ref_maps.get(lab.batch_id, {}))
            for n in ns:
                soft_nodes[n.id] = n
            soft_edges.extend(es)
        return AnalyzerResult(nodes=list(soft_nodes.values()),
                              edges=self._dedup_edges(soft_edges), plugin=self.name)

    # ---- 确定性事实构建(纯读已落库硬节点/边, 零新扫描) ----

    def _build_facts(self, nodes, edges):
        files = {n.id: n for n in nodes if n.kind == NodeKind.FILE.value}
        if not files:
            return {}
        path_to_fid = {n.name: n.id for n in files.values()}   # node.file(=path) → file 节点 id
        fact = {fid: {"endpoint": False, "tables": set(), "functions": 0,
                      "imp_out": 0, "imp_in": 0} for fid in files}
        node_file = {}                                          # 任意 node → 它所属 file 节点 id
        for n in nodes:
            fid = path_to_fid.get(n.file or "")
            node_file[n.id] = fid
            if fid is None:
                continue
            if n.kind == NodeKind.BACKEND_ENDPOINT.value:
                fact[fid]["endpoint"] = True
            elif n.kind == NodeKind.BACKEND_FUNCTION.value:
                fact[fid]["functions"] += 1
        for e in edges:
            if e.kind == EdgeKind.IMPORTS.value:
                if e.source in files:
                    fact[e.source]["imp_out"] += 1
                if e.target in files:
                    fact[e.target]["imp_in"] += 1
            elif e.kind in (EdgeKind.READS_TABLE.value, EdgeKind.WRITES_TABLE.value):
                sfid = node_file.get(e.source)
                if sfid is not None:
                    fact[sfid]["tables"].add(e.target)
        return {fid: (files[fid].name, fact[fid]) for fid in files}

    # ---- 按目录聚类 + closed-world ref 编号 ----

    def _batch_by_dir(self, facts):
        by_dir: dict[str, list] = {}
        for fid, (path, fc) in facts.items():
            d = path.rsplit("/", 1)[0] if "/" in path else "."
            by_dir.setdefault(d, []).append((fid, path, fc))
        batches: list[LayerRequest] = []
        ref_maps: dict[str, dict[str, str]] = {}
        for d in sorted(by_dir):
            items = sorted(by_dir[d], key=lambda x: x[1])[: self._max_files]   # 确定性 + 截断
            ff: list[FileFact] = []
            ref_to_id: dict[str, str] = {}
            for i, (fid, path, fc) in enumerate(items):
                ref = f"f{i + 1}"
                ff.append(FileFact(
                    ref=ref, path=path, has_endpoint=fc["endpoint"],
                    reads_tables=len(fc["tables"]), defines_functions=fc["functions"],
                    imports_out=fc["imp_out"], imports_in=fc["imp_in"]))
                ref_to_id[ref] = fid
            batches.append(LayerRequest(batch_id=d, files=tuple(ff)))
            ref_maps[d] = ref_to_id
        return batches, ref_maps

    # ---- 解析回填(软产物; referential-integrity 由 base.validate_soft_result 兜底) ----

    def _to_soft(self, project_id, label: LayerLabel, ref_to_id):
        soft_nodes: list[GraphNode] = []
        soft_edges: list[GraphEdge] = []
        seen_layers: set[str] = set()
        for fref, role in label.roles:
            if role not in LAYER_ROLES:          # 越界 layer(枚举外)→ 剔除
                continue
            fid = ref_to_id.get(fref)            # 越界 ref(造的假)→ 剔除
            if fid is None:
                continue
            layer_id = f"{project_id}:arch_layer:{role}"
            if layer_id not in seen_layers:
                seen_layers.add(layer_id)
                soft_nodes.append(GraphNode(
                    id=layer_id, kind=NodeKind.ARCH_LAYER, name=role, project_id=project_id,
                    meta={"derived_by": self.name, "confidence": _CONF}))
            soft_edges.append(GraphEdge(
                source=fid, target=layer_id, kind=EdgeKind.PLAYS_ROLE, confidence=_CONF))
        return soft_nodes, soft_edges

    @staticmethod
    def _dedup_edges(edges):
        seen, out = set(), []
        for e in edges:
            k = (e.source, e.target, e.kind)
            if k in seen:
                continue
            seen.add(k)
            out.append(e)
        return out

    def _safe_label(self, batches):
        """labeler 抛错 → fail-soft(整批放弃, 各 batch roles=()), 不拖垮 analyze。"""
        try:
            return self._labeler.label(batches)
        except Exception as exc:  # noqa: BLE001 — 标注器抖动不拖垮 analyzer
            logger.warning("[arch_layer] labeler failed: %r", exc)
            return [LayerLabel(req.batch_id) for req in batches]
