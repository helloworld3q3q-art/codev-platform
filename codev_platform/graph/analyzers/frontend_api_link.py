"""前端→后端 API 链接 analyzer(A3)—— 给静态层漏检的前端文件补 calls_api 软边。

定位(综合理解层第三个 analyzer, 对称 A1 业务域 / A2 架构分层): 静态基础层
(url_registry 解析常量声明 + 内联 axios/fetch 字面量扫描)抓不到**业务封装的内联请求**——
典型如 `request({url: BIZ_CONST, method})` 用自定义 request 封装 + 业务命名, URL 不在调用点
字面量也不在集中常量文件。这类前端文件有 frontend_component/module 节点却**连不上后端**。

A3 读这些"静态盲"前端文件的源码语义, 问 LLM"它调了候选清单里的哪些后端端点", 产
INFERRED_API_CALL 软节点 + CALLS_API_INFERRED 软边(指向真实 backend_endpoint)。

边界划分(与 url_registry 互补, 不重写它):
- url_registry / 内联扫描 = **静态基础层**(确定性, 产硬 FRONTEND_API_CALL + 静态 calls_api);
- A3 = **LLM 补充层**(软, 只处理静态层没连上的前端文件), 软硬物理隔离, impact 默认只信静态。

grounding(双层抗幻觉):
- closed-world: 候选端点编号 ep1/ep2 喂 labeler, labeler 只能回引清单内 ref —— 越界 ref 剔除;
- referential-integrity: 软边 target 必须是真实 backend_endpoint 硬节点 id(base.validate 兜底)。

确定性部分全在本模块(找盲文件 / 裁源码 / 候选编号 / 解析回填 / 缓存), LLM 隔离在
ApiLinkLabeler 后(FakeApiLinkLabeler 可全确定性测)。无 repo / 无 labeler → no-op。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from codev_platform.graph.analyzers.api_link_labeler import (
    ApiLinkLabel,
    ApiLinkLabeler,
    ApiLinkRequest,
    EndpointRef,
    FrontendFile,
)
from codev_platform.graph.schema import (
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)

logger = logging.getLogger(__name__)

_CONF = 0.6                   # 软产物置信(< 1.0; LLM 推断比 A1/A2 归类更不确定 → 略低)
_MAX_FILES = 200             # 单轮喂 LLM 的盲前端文件上限(成本护栏)
_MAX_ENDPOINTS = 400         # 候选端点上限(防爆 token; 超额按 url 排序确定性截断)
_SNIPPET_MAX = 60            # 单文件喂 LLM 的请求相关行数上限(裁剪防爆 token)

# 含潜在 API 调用的前端文件节点 kind(有这些节点 = 是前端代码文件)。
_FRONTEND_KINDS = frozenset({
    NodeKind.FRONTEND_COMPONENT.value, NodeKind.FRONTEND_MODULE.value,
    NodeKind.FRONTEND_ROUTE.value,
})
_FRONTEND_SUFFIXES = (".vue", ".js", ".jsx", ".ts", ".tsx")
# 请求相关行的廉价信号(裁源码只留这些行 + 上下文, 不喂整文件): 自定义封装多叫 request/api/http。
_REQUEST_HINT = re.compile(r"\b(request|axios|fetch|http|api|url|method|post|get|put|delete|patch)\b", re.I)


class FrontendApiLinkAnalyzer:
    """前端 API 链接 analyzer。LLM 经 ApiLinkLabeler 注入(不直接依赖 brain);源码经 repo 读。

    repo_path 经 set_context 注入(ingest 的 _analyzers_pass 在跑前设)。无 repo → applies=False
    (no-op): A3 必须读源码, 拿不到源码不瞎猜。
    """

    name = "frontend_api_link"

    def __init__(self, labeler: ApiLinkLabeler, *, repo_path: Path | None = None,
                 cache_dir: Path | None = None, max_files: int = _MAX_FILES,
                 max_endpoints: int = _MAX_ENDPOINTS) -> None:
        self._labeler = labeler
        self._repo = Path(repo_path) if repo_path else None
        self._cache_dir = cache_dir
        self._max_files = max_files
        self._max_endpoints = max_endpoints

    def set_context(self, repo_path: Path | None) -> None:
        """ingest 在 analyze 前注入主仓根(A3 读源码用)。其他 analyzer 无此方法, 不受影响。"""
        self._repo = Path(repo_path) if repo_path else None

    # ---- Analyzer 协议 ----

    def applies(self, nodes: list[GraphNode]) -> bool:
        if self._repo is None or not self._repo.is_dir():
            return False
        has_frontend = any(n.kind in _FRONTEND_KINDS for n in nodes)
        has_backend = any(n.kind == NodeKind.BACKEND_ENDPOINT.value for n in nodes)
        if not (has_frontend and has_backend):
            return False
        avail = getattr(self._labeler, "available", None)
        return avail() if callable(avail) else True

    def analyze(self, project_id: str, nodes: list[GraphNode],
                edges: list[GraphEdge]) -> AnalyzerResult:
        endpoints = self._endpoint_refs(nodes)
        if not endpoints:
            return AnalyzerResult(plugin=self.name)
        blind_files = self._blind_frontend_files(nodes, edges)
        if not blind_files:
            return AnalyzerResult(plugin=self.name)

        requests, ref_to_epid = self._build_requests(blind_files, endpoints)
        if not requests:
            return AnalyzerResult(plugin=self.name)
        labels = self._labels_with_cache(project_id, requests)

        soft_nodes: list[GraphNode] = []
        soft_edges: list[GraphEdge] = []
        seen_node: set[str] = set()
        for lab in labels:
            ns, es = self._to_soft(project_id, lab, ref_to_epid)
            for n in ns:
                if n.id not in seen_node:
                    seen_node.add(n.id)
                    soft_nodes.append(n)
            soft_edges.extend(es)
        return AnalyzerResult(nodes=soft_nodes,
                              edges=self._dedup_edges(soft_edges), plugin=self.name)

    # ---- 确定性: 候选端点编号(closed-world) ----

    def _endpoint_refs(self, nodes) -> list[tuple[str, GraphNode]]:
        eps = sorted((n for n in nodes if n.kind == NodeKind.BACKEND_ENDPOINT.value
                      and (n.meta or {}).get("url")),
                     key=lambda n: str((n.meta or {}).get("url")))
        eps = eps[: self._max_endpoints]                       # 成本护栏: 确定性截断
        return [(f"ep{i + 1}", n) for i, n in enumerate(eps)]

    # ---- 确定性: 找静态盲前端文件(有前端节点, 但该文件无 calls_api 出边) ----

    def _blind_frontend_files(self, nodes, edges) -> list[str]:
        """前端代码文件(有 frontend_* 节点)中, **该文件所有节点都没有 calls_api 出边** 的 →
        静态层没连上后端 = A3 的处理对象。已被静态层连上的文件跳过(不重复 / 不与硬边打架)。"""
        node_file: dict[str, str] = {n.id: n.file for n in nodes if n.file}
        frontend_files: set[str] = set()
        for n in nodes:
            if n.kind in _FRONTEND_KINDS and n.file:
                frontend_files.add(n.file)
        # 已有 calls_api / calls_api_inferred 出边的文件(静态层已连或本轮已推断)→ 不再处理。
        linked_files: set[str] = set()
        link_kinds = {EdgeKind.CALLS_API.value, EdgeKind.CALLS_API_INFERRED.value}
        for e in edges:
            if e.kind in link_kinds:
                f = node_file.get(e.source)
                if f:
                    linked_files.add(f)
        return sorted(frontend_files - linked_files)

    # ---- 确定性: 读源码 + 裁请求相关行 → ApiLinkRequest ----

    def _build_requests(self, blind_files, endpoints):
        ep_refs = tuple(EndpointRef(
            ref=ref, url=str((n.meta or {}).get("url") or ""),
            method=str((n.meta or {}).get("http_method") or "POST").upper(),
            name=n.name) for ref, n in endpoints)
        ref_to_epid = {ref: n.id for ref, n in endpoints}
        requests: list[ApiLinkRequest] = []
        for rel in blind_files[: self._max_files]:
            snippet = self._read_snippet(rel)
            if not snippet:
                continue
            requests.append(ApiLinkRequest(
                file=FrontendFile(path=rel, snippet=snippet), endpoints=ep_refs))
        return requests, ref_to_epid

    def _read_snippet(self, rel: str) -> str:
        """读前端文件, 只留含请求信号的行 + 1 行上下文(确定性裁剪, 防爆 token)。"""
        if self._repo is None:
            return ""
        p = self._repo / rel
        if p.suffix not in _FRONTEND_SUFFIXES or not p.is_file():
            return ""
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        keep: list[str] = []
        n = len(lines)
        picked: set[int] = set()
        for i, line in enumerate(lines):
            if _REQUEST_HINT.search(line):
                for j in range(max(0, i - 1), min(n, i + 2)):
                    picked.add(j)
        for i in sorted(picked):
            keep.append(lines[i].strip())
            if len(keep) >= _SNIPPET_MAX:
                break
        return "\n".join(keep)

    # ---- 解析回填(软产物; referential-integrity 由 base.validate 兜底) ----

    def _to_soft(self, project_id, label: ApiLinkLabel, ref_to_epid):
        soft_nodes: list[GraphNode] = []
        soft_edges: list[GraphEdge] = []
        for ref in label.endpoint_refs:
            ep_id = ref_to_epid.get(ref)        # 越界 ref(造的假端点)→ None 剔除(grounding)
            if ep_id is None:
                continue
            # 软调用节点 id 含目标 endpoint → 同文件调多端点各一节点, 重跑幂等。
            call_id = f"{project_id}:inferred_api_call:{label.path}->{ep_id}"
            soft_nodes.append(GraphNode(
                id=call_id, kind=NodeKind.INFERRED_API_CALL, name=label.path.rsplit("/", 1)[-1],
                project_id=project_id, file=label.path,
                meta={"derived_by": self.name, "confidence": _CONF, "target_endpoint": ep_id}))
            soft_edges.append(GraphEdge(
                source=call_id, target=ep_id, kind=EdgeKind.CALLS_API_INFERRED,
                confidence=_CONF, meta={"derived_by": self.name}))
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

    def _safe_label(self, batch):
        try:
            return self._labeler.label(batch)
        except Exception as exc:  # noqa: BLE001 — 标注器抖动不拖垮 analyzer
            logger.warning("[frontend_api_link] labeler failed: %r", exc)
            return [ApiLinkLabel(req.file.path) for req in batch]

    # ---- 缓存(per-file fingerprint, 复用 A1/A2 模式) ----

    def _labels_with_cache(self, project_id, requests):
        cache = self._load_cache(project_id)
        keyed = [(req, self._cache_key(req)) for req in requests]
        valid_keys = {k for _, k in keyed}
        path_to_key = {req.file.path: k for req, k in keyed}
        labels, to_label = [], []
        for req, key in keyed:
            ent = cache.get(key)
            if ent is not None:
                labels.append(ApiLinkLabel(
                    req.file.path, tuple(ent.get("endpoint_refs", ()))))
            else:
                to_label.append(req)
        if to_label:
            for lab in self._safe_label(to_label):
                labels.append(lab)
                key = path_to_key.get(lab.path)
                if key is not None:
                    cache[key] = {"endpoint_refs": list(lab.endpoint_refs)}
        pruned = {k: v for k, v in cache.items() if k in valid_keys}   # GC 孤儿
        if to_label or len(pruned) != len(cache):
            self._save_cache(project_id, pruned)
        return labels

    def _cache_key(self, req: ApiLinkRequest):
        # snippet + 候选端点 url 集 + labeler 签名(换模型/改 prompt 失效)。源码或候选变 → 重标。
        payload = json.dumps(
            {"path": req.file.path, "snip": req.file.snippet,
             "eps": sorted(e.url for e in req.endpoints),
             "sig": getattr(self._labeler, "signature", "")},
            sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cache_path(self, project_id):
        if self._cache_dir is not None:
            base = self._cache_dir
        else:
            try:
                from codev_platform.core.paths import data_root
                base = data_root() / "frontend_api_link_cache"
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
