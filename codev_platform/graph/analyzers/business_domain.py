"""业务域映射 analyzer(A1-2)—— 在硬骨架上把 endpoint→表链路归类成业务域软节点。

实现 A1-1 的 Analyzer 协议。LLM 全部隔离在 DomainLabeler 接口后(A1-2a 用 FakeLabeler 可
全确定性测试; A1-2b 接 BrainDomainLabeler)。本模块只做**确定性**部分:聚类 / grounding 数据
准备 / 解析回填 / 缓存。

聚类(四专家会诊纠错): 不用弱连通分量(真实图被共享表/util 连成巨型分量, 退化成整图一簇)。
改 **endpoint 种子 + 正向 BFS 收下游表 + hub 抑制 + union-find 合并**:
- 每个 backend_endpoint 正向 BFS 收下游 db_table = 一个业务单元;
- hub 表(被 ≥ 分位阈值个 endpoint 共享, 如 user/audit/config)**不作合并依据**(否则全图粘连);
- 两 endpoint 共享非 hub "专属表" → union-find 合并成一个业务域 cluster。
全程确定性(node.id 排序 + union-find min-id 作根, 无随机源), 纯 stdlib 不引 networkx。

grounding(closed-world 进类型): cluster 内实体编号成 ref(e1/t2)喂 labeler, labeler 只能回引
ref —— 越界 ref 解析时剔除。软产物的 referential-integrity(悬空软边丢)+ confidence 钳制由
A1-1 validate_soft_result 统一兜底, 本模块**不重复**。

缓存: per-cluster fingerprint = hash(cluster 成员 + labeler.signature[model+prompt_version])。
cluster 结构变 / 换模型 / 改 prompt → 键变失效; 否则命中跳过 labeler。member 每次从当前图重绑,
不从缓存复用 node id(防改名后悬空)。
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from codev_platform.core.paths import data_root
from codev_platform.graph.analyzers.domain_labeler import (
    ClusterLabel,
    ClusterMember,
    ClusterRequest,
    DomainLabeler,
)
from codev_platform.graph.schema import (
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)

logger = logging.getLogger(__name__)

_CONF = 0.7                  # 软产物置信(< 1.0; validate_soft_result 也会兜底钳制)
_BFS_MAX_DEPTH = 6           # endpoint→表 收敛深度上限(防爆炸)
_HUB_MIN_SHARE = 3           # 至少被这么多 endpoint 共享才可能算 hub(小项目保护)
_MAX_DOMAIN_LEN = 12         # 域名长度上限(超则判非法丢弃)
# 同义域名归一(防碎片化: 同业务被标成不同词 → 归并到一个软节点)。可扩展。
_ALIAS: dict[str, str] = {"下单": "订单", "交易单": "订单", "行情数据": "行情"}


class _UnionFind:
    """确定性并查集(min-id 作根, 结果与合并顺序无关)。"""

    def __init__(self, items: list[str]) -> None:
        self._parent = {x: x for x in items}

    def find(self, x: str) -> str:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        hi, lo = (ra, rb) if ra > rb else (rb, ra)  # 大 id 挂到小 id, 确定性
        self._parent[hi] = lo


class BusinessDomainAnalyzer:
    """业务域 analyzer。LLM 经 DomainLabeler 注入(不直接依赖 brain)。"""

    name = "business_domain"

    def __init__(self, labeler: DomainLabeler, *, cache_dir: Path | None = None,
                 max_clusters: int = 200) -> None:
        self._labeler = labeler
        self._cache_dir = cache_dir
        self._max_clusters = max_clusters

    # ---- Analyzer 协议 ----

    def applies(self, nodes: list[GraphNode]) -> bool:
        return any(n.kind == NodeKind.BACKEND_ENDPOINT.value for n in nodes)

    def analyze(self, project_id: str, nodes: list[GraphNode],
                edges: list[GraphEdge]) -> AnalyzerResult:
        clusters, by_id = self._cluster(nodes, edges)
        if not clusters:
            return AnalyzerResult(plugin=self.name)
        if len(clusters) > self._max_clusters:  # 成本护栏: 超量截断 + 告警(fail-soft)
            logger.warning("[business_domain] %d clusters > cap %d, truncating",
                           len(clusters), self._max_clusters)
            clusters = clusters[: self._max_clusters]

        keyed: list[tuple[ClusterRequest, str]] = []
        ref_maps: dict[str, dict[str, str]] = {}
        for cid, eps, tables in clusters:
            req, ref_to_id = self._to_request(cid, eps, tables, by_id)
            keyed.append((req, self._cache_key(req)))
            ref_maps[cid] = ref_to_id

        labels = self._labels_with_cache(project_id, keyed)

        soft_nodes: dict[str, GraphNode] = {}   # 按 dom_id 去重(同名域复用; nodes 表 INSERT 非 REPLACE)
        soft_edges: list[GraphEdge] = []
        for lab in labels:
            ns, es = self._to_soft(project_id, lab, ref_maps.get(lab.cluster_id, {}))
            for n in ns:
                soft_nodes[n.id] = n
            soft_edges.extend(es)
        return AnalyzerResult(nodes=list(soft_nodes.values()),
                              edges=self._dedup_edges(soft_edges), plugin=self.name)

    # ---- 聚类(确定性) ----

    def _cluster(self, nodes, edges):
        by_id = {n.id: n for n in nodes}
        fwd: dict[str, list[str]] = {}
        for e in edges:
            fwd.setdefault(e.source, []).append(e.target)
        for k in fwd:
            fwd[k].sort()  # 确定性遍历

        endpoints = sorted(n.id for n in nodes
                           if n.kind == NodeKind.BACKEND_ENDPOINT.value)
        tables = {n.id for n in nodes if n.kind == NodeKind.DB_TABLE.value}
        if not endpoints:
            return [], by_id

        ep_tables = {ep: self._downstream_tables(ep, fwd, tables) for ep in endpoints}
        hub = self._hub_tables(ep_tables)

        uf = _UnionFind(endpoints)
        table_to_eps: dict[str, list[str]] = {}
        for ep in endpoints:
            for t in ep_tables[ep]:
                if t not in hub:
                    table_to_eps.setdefault(t, []).append(ep)
        for eps in table_to_eps.values():  # 同一专属表的 endpoint 互相合并
            for other in eps[1:]:
                uf.union(eps[0], other)

        groups: dict[str, list[str]] = {}
        for ep in endpoints:
            groups.setdefault(uf.find(ep), []).append(ep)
        clusters = []
        for root in sorted(groups):
            eps = sorted(groups[root])
            ctables = sorted({t for ep in eps for t in ep_tables[ep] if t not in hub})
            clusters.append((root, eps, ctables))
        return clusters, by_id

    @staticmethod
    def _downstream_tables(ep, fwd, tables):
        """从 endpoint 正向 BFS 收下游 db_table(深度限制)。"""
        out, visited, queue = set(), {ep}, [(ep, 0)]
        while queue:
            nid, depth = queue.pop(0)
            if depth >= _BFS_MAX_DEPTH:
                continue
            for nbr in fwd.get(nid, ()):
                if nbr in visited:
                    continue
                visited.add(nbr)
                if nbr in tables:
                    out.add(nbr)
                queue.append((nbr, depth + 1))
        return out

    @staticmethod
    def _hub_tables(ep_tables):
        """扇入 ≥ P95 分位(且 ≥ _HUB_MIN_SHARE)的表 = hub, 不作合并依据。"""
        fanin: dict[str, int] = {}
        for ts in ep_tables.values():
            for t in ts:
                fanin[t] = fanin.get(t, 0) + 1
        if not fanin:
            return set()
        counts = sorted(fanin.values())
        p95 = counts[min(len(counts) - 1, int(len(counts) * 0.95))]
        threshold = max(_HUB_MIN_SHARE, p95)
        return {t for t, c in fanin.items() if c >= threshold}

    # ---- grounding 数据准备 ----

    @staticmethod
    def _to_request(cid, eps, tables, by_id):
        members, ref_to_id = [], {}
        for i, ep in enumerate(eps):
            ref = f"e{i + 1}"
            members.append(ClusterMember(ref=ref, kind="endpoint", name=by_id[ep].name))
            ref_to_id[ref] = ep
        for i, t in enumerate(tables):
            ref = f"t{i + 1}"
            members.append(ClusterMember(ref=ref, kind="table", name=by_id[t].name))
            ref_to_id[ref] = t
        return ClusterRequest(cluster_id=cid, members=tuple(members)), ref_to_id

    # ---- 解析回填(软产物; referential-integrity 由 A1-1 validate 兜底) ----

    def _to_soft(self, project_id, label, ref_to_id):
        domain = self._normalize_domain(label.domain)
        if not domain:
            return [], []
        dom_id = f"{project_id}:business_domain:{domain}"  # 同名域复用 id
        dom = GraphNode(id=dom_id, kind=NodeKind.BUSINESS_DOMAIN, name=domain,
                        project_id=project_id,
                        meta={"derived_by": self.name, "confidence": _CONF})
        edges = []
        for ref in label.member_refs:
            nid = ref_to_id.get(ref)        # ref 越界(造的假 ref)→ 剔除
            if nid is None:
                continue
            edges.append(GraphEdge(source=nid, target=dom_id,
                                   kind=EdgeKind.BELONGS_TO_DOMAIN, confidence=_CONF))
        return [dom], edges

    @staticmethod
    def _normalize_domain(domain):
        if not domain or not isinstance(domain, str):
            return None
        d = "".join(domain.split()).strip("·.,。、:：-—")
        d = _ALIAS.get(d, d)
        if not d or len(d) > _MAX_DOMAIN_LEN:
            return None
        return d

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

    # ---- 缓存(per-cluster fingerprint) ----

    def _labels_with_cache(self, project_id, keyed):
        cache = self._load_cache(project_id)
        cid_to_key = {req.cluster_id: key for req, key in keyed}
        labels, to_label = [], []
        for req, key in keyed:
            ent = cache.get(key)
            if ent is not None:
                labels.append(ClusterLabel(req.cluster_id, ent.get("domain"),
                                           tuple(ent.get("member_refs", ()))))
            else:
                to_label.append(req)
        if to_label:
            for lab in self._safe_label(to_label):
                labels.append(lab)
                key = cid_to_key.get(lab.cluster_id)
                if key is not None:
                    cache[key] = {"domain": lab.domain,
                                  "member_refs": list(lab.member_refs)}
            self._save_cache(project_id, cache)
        return labels

    def _safe_label(self, batch):
        """labeler 抛错 → fail-soft(整批放弃, 各 cluster domain=None), 不拖垮 analyze。"""
        try:
            return self._labeler.label(batch)
        except Exception as exc:  # noqa: BLE001 — 标注器抖动不拖垮 analyzer
            logger.warning("[business_domain] labeler failed: %r", exc)
            return [ClusterLabel(req.cluster_id, None) for req in batch]

    def _cache_key(self, req):
        payload = json.dumps(
            {"m": [[m.ref, m.kind, m.name] for m in req.members],
             "sig": getattr(self._labeler, "signature", "")},
            sort_keys=True, ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cache_path(self, project_id):
        if self._cache_dir is not None:
            base = self._cache_dir
        else:
            try:
                base = data_root() / "business_domain_cache"
            except Exception:  # noqa: BLE001 — data_root 不可用 → 不缓存, 不报错
                return None
        return base / f"{project_id}.json"

    def _load_cache(self, project_id):
        path = self._cache_path(project_id)
        if path is None or not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("entries", {})
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
