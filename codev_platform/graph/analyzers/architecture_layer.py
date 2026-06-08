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

import hashlib
import json
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

    def __init__(self, labeler: LayerLabeler, *, cache_dir=None,
                 max_batches: int = _MAX_BATCHES,
                 max_files_per_batch: int = _MAX_FILES_PER_BATCH) -> None:
        self._labeler = labeler
        self._cache_dir = cache_dir
        self._max_batches = max_batches
        self._max_files = max_files_per_batch

    # ---- Analyzer 协议 ----

    def applies(self, nodes: list[GraphNode]) -> bool:
        # graph 不建 FILE kind 节点; 文件单位用 node.file 属性聚合(endpoint/function/module 都带 file)。
        if not any(n.file for n in nodes):
            return False
        avail = getattr(self._labeler, "available", None)
        return avail() if callable(avail) else True

    def analyze(self, project_id: str, nodes: list[GraphNode],
                edges: list[GraphEdge]) -> AnalyzerResult:
        by_file, file_nodes = self._build_facts(nodes, edges)   # path→fact, path→[该文件硬节点 id]
        if not by_file:
            return AnalyzerResult(plugin=self.name)
        batches, ref_maps = self._batch_by_dir(by_file)
        if len(batches) > self._max_batches:           # 成本护栏: 超量截断(fail-soft)
            logger.warning("[arch_layer] %d batches > cap %d, truncating",
                           len(batches), self._max_batches)
            batches = batches[: self._max_batches]
        labels = self._labels_with_cache(project_id, batches)

        soft_nodes: dict[str, GraphNode] = {}          # 按 layer_id 去重(同角色软节点复用)
        soft_edges: list[GraphEdge] = []
        for lab in labels:
            ns, es = self._to_soft(project_id, lab, ref_maps.get(lab.batch_id, {}), file_nodes)
            for n in ns:
                soft_nodes[n.id] = n
            soft_edges.extend(es)
        return AnalyzerResult(nodes=list(soft_nodes.values()),
                              edges=self._dedup_edges(soft_edges), plugin=self.name)

    # ---- 确定性事实构建(纯读已落库硬节点/边, 零新扫描) ----

    def _build_facts(self, nodes, edges):
        """按 node.file 聚合文件单位(graph 无 FILE kind 节点, 用 .file 属性)。零新扫描。
        返回 (path → fact, path → [该文件的硬节点 id 列表])。"""
        by_file: dict[str, dict] = {}
        file_nodes: dict[str, list[str]] = {}
        node_file: dict[str, str] = {}
        for n in nodes:
            if not n.file:
                continue
            node_file[n.id] = n.file
            file_nodes.setdefault(n.file, []).append(n.id)
            fc = by_file.setdefault(n.file, {"endpoint": False, "tables": set(),
                                             "functions": 0, "imp_out": 0, "imp_in": 0})
            if n.kind == NodeKind.BACKEND_ENDPOINT.value:
                fc["endpoint"] = True
            elif n.kind == NodeKind.BACKEND_FUNCTION.value:
                fc["functions"] += 1
        for e in edges:
            sf = node_file.get(e.source)
            if e.kind == EdgeKind.IMPORTS.value:
                tf = node_file.get(e.target)
                if sf and sf in by_file:
                    by_file[sf]["imp_out"] += 1
                if tf and tf in by_file:
                    by_file[tf]["imp_in"] += 1
            elif e.kind in (EdgeKind.READS_TABLE.value, EdgeKind.WRITES_TABLE.value):
                if sf and sf in by_file:
                    by_file[sf]["tables"].add(e.target)
        return by_file, file_nodes

    # ---- 按目录聚类 + closed-world ref 编号 ----

    def _batch_by_dir(self, by_file):
        by_dir: dict[str, list] = {}
        for path, fc in by_file.items():
            d = path.rsplit("/", 1)[0] if "/" in path else "."
            by_dir.setdefault(d, []).append((path, fc))
        batches: list[LayerRequest] = []
        ref_maps: dict[str, dict[str, str]] = {}
        for d in sorted(by_dir):
            items = sorted(by_dir[d], key=lambda x: x[0])[: self._max_files]   # 确定性 + 截断
            ff: list[FileFact] = []
            ref_to_path: dict[str, str] = {}
            for i, (path, fc) in enumerate(items):
                ref = f"f{i + 1}"
                ff.append(FileFact(
                    ref=ref, path=path, has_endpoint=fc["endpoint"],
                    reads_tables=len(fc["tables"]), defines_functions=fc["functions"],
                    imports_out=fc["imp_out"], imports_in=fc["imp_in"]))
                ref_to_path[ref] = path
            batches.append(LayerRequest(batch_id=d, files=tuple(ff)))
            ref_maps[d] = ref_to_path
        return batches, ref_maps

    # ---- 解析回填(软产物; referential-integrity 由 base.validate_soft_result 兜底) ----

    def _to_soft(self, project_id, label: LayerLabel, ref_to_path, file_nodes):
        soft_nodes: list[GraphNode] = []
        soft_edges: list[GraphEdge] = []
        seen_layers: set[str] = set()
        for fref, role in label.roles:
            if role not in LAYER_ROLES:          # 越界 layer(枚举外)→ 剔除
                continue
            path = ref_to_path.get(fref)         # 越界 ref(造的假)→ 剔除
            if path is None:
                continue
            layer_id = f"{project_id}:arch_layer:{role}"
            if layer_id not in seen_layers:
                seen_layers.add(layer_id)
                soft_nodes.append(GraphNode(
                    id=layer_id, kind=NodeKind.ARCH_LAYER, name=role, project_id=project_id,
                    meta={"derived_by": self.name, "confidence": _CONF}))
            # graph 无 FILE 节点: 该文件的每个硬节点(function/endpoint/...) plays_role layer。
            for nid in file_nodes.get(path, ()):
                soft_edges.append(GraphEdge(
                    source=nid, target=layer_id, kind=EdgeKind.PLAYS_ROLE, confidence=_CONF))
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

    # ---- 缓存(per-batch fingerprint, 复用 business_domain 模式; 未来可抽公共 helper) ----

    def _labels_with_cache(self, project_id, batches):
        cache = self._load_cache(project_id)
        keyed = [(req, self._cache_key(req)) for req in batches]
        valid_keys = {k for _, k in keyed}        # 本轮 fingerprint(cache GC 据此清孤儿)
        bid_to_key = {req.batch_id: k for req, k in keyed}
        labels, to_label = [], []
        for req, key in keyed:
            ent = cache.get(key)
            if ent is not None:
                labels.append(LayerLabel(
                    req.batch_id, tuple((r[0], r[1]) for r in ent.get("roles", ()))))
            else:
                to_label.append(req)
        if to_label:
            for lab in self._safe_label(to_label):
                labels.append(lab)
                key = bid_to_key.get(lab.batch_id)
                if key is not None:
                    cache[key] = {"roles": [[f, lr] for f, lr in lab.roles]}
        pruned = {k: v for k, v in cache.items() if k in valid_keys}   # GC 孤儿(防单调增长)
        if to_label or len(pruned) != len(cache):
            self._save_cache(project_id, pruned)
        return labels

    def _cache_key(self, req):
        payload = json.dumps(
            {"f": [[f.ref, f.path, f.has_endpoint, f.reads_tables, f.imports_out,
                    f.imports_in, f.defines_functions] for f in req.files],
             "sig": getattr(self._labeler, "signature", "")},
            sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cache_path(self, project_id):
        if self._cache_dir is not None:
            base = self._cache_dir
        else:
            try:
                from codev_platform.core.paths import data_root
                base = data_root() / "arch_layer_cache"
            except Exception:  # noqa: BLE001 — data_root 不可用 → 不缓存, 不报错
                return None
        return base / f"{project_id}.json"

    def _load_cache(self, project_id):
        path = self._cache_path(project_id)
        if path is None or not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("entries", {})
        except (OSError, ValueError):
            return {}

    def _save_cache(self, project_id, entries):
        path = self._cache_path(project_id)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"entries": entries}, ensure_ascii=False),
                            encoding="utf-8")
        except OSError:
            pass
