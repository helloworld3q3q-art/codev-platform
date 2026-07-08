"""图谱结构审计 —— roadmap-2026-06-07 Phase 3 (MVP 切片)。

让"影响分析的依据可审计": 纯读 per-project graph store, 报结构完整性问题。
分 **errors**(真 bug, 会让 impact/BFS 出错) 与 **warnings**(可疑, 待 review):

errors:
- dangling_edges: 边指向不存在的节点 id (断链 → BFS 漏/错)。
- cross_project: 节点/边的 project_id != 目标 pid (多租户串台泄漏, 红线)。

warnings:
- duplicate_nodes: 同 (kind, name, file) 多个 id (同一逻辑对象建了多次, 影响去重)。
- low_confidence_edges: 硬边 confidence < 阈值 (fuzzy/regex 推断, 不应进高风险结论)。

**MVP 刻意不做**(plan Phase 3 重型部分): provenance 字段扩展(source_kind/parser_name/
parser_version/index_manifest_id 落每条边)+ 冲突消解(同 endpoint 被多 parser 给不同目标)
—— 那要 schema 迁移 + 改每个插件, 留后续。本切片只在**现有数据**上做结构体检。
"""
from __future__ import annotations

from collections import Counter, defaultdict

from codev_platform.graph.schema import (
    EdgeKind,
    NodeKind,
    edge_provenance,
    is_soft_edge_kind,
    is_soft_node_kind,
)
from codev_platform.graph.store import (
    GraphStore,
    GraphStoreUnreadable,
    _graph_backend,
    open_store,
)

_LOW_CONF = 0.7
_SAMPLE = 10
# 这些 kind 的 name 在同一 file 内**合法重复**, 不当"重复节点"报:
# db_column —— 同一文件多张表常有同名列 (id / meta_json / plugin ...), 各属不同表非重复。
_DUP_EXEMPT_KINDS = frozenset({NodeKind.DB_COLUMN.value})


def _canonical_soft_plugin() -> str:
    """软产物(ARCH_LAYER/BUSINESS_DOMAIN...)唯一合法来源 plugin(见 ingest._analyzers_pass)。"""
    try:
        from codev_platform.graph.ingest import ANALYZERS_PLUGIN
        return ANALYZERS_PLUGIN
    except Exception:  # noqa: BLE001
        return "builtin.analyzers"


def _node_meta_str(node, key: str, default: str = "") -> str:
    value = (node.meta or {}).get(key)
    return value if isinstance(value, str) else default


def _api_node_sample(node) -> dict:
    return {
        "id": node.id,
        "name": node.name,
        "file": node.file,
        "url": _node_meta_str(node, "url"),
        "http_method": _node_meta_str(node, "http_method"),
    }


def _api_link_status(frontend_count: int, backend_count: int,
                     calls_api_count: int, unlinked_count: int) -> str:
    if frontend_count == 0:
        return "no_frontend_api"
    if backend_count == 0:
        return "frontend_without_backend_endpoints"
    if calls_api_count == 0:
        return "frontend_backend_unlinked"
    if unlinked_count:
        return "partial_frontend_linkage"
    return "linked"


def _api_link_diagnosis(status: str) -> str:
    return {
        "no_frontend_api": "未发现前端 API 调用节点。",
        "frontend_without_backend_endpoints": (
            "发现前端 API, 但没有后端 endpoint; 通常是后端源码未纳入或后端扫描器未覆盖。"
        ),
        "frontend_backend_unlinked": (
            "前后端节点都存在, 但没有 calls_api; 通常是后端源码覆盖不足、URL/method 不匹配或 linker 覆盖不足。"
        ),
        "partial_frontend_linkage": "部分前端 API 未匹配到后端 endpoint, 建议核对未链接样本。",
        "linked": "前端 API 均已建立 calls_api 硬边。",
    }[status]


def _api_link_coverage(g) -> dict:
    """前端 API 到后端 endpoint 的确定性链路覆盖诊断。

    这是读侧 warning, 不参与 clean/error 结论: 缺后端源码或扫描器不足都不该被伪装成结构错误,
    但必须在审计里可见, 便于判断下一步是补仓库、补 parser, 还是补 linker。
    """
    frontend_nodes = [n for n in g.nodes if n.kind == NodeKind.FRONTEND_API_CALL.value]
    backend_nodes = [n for n in g.nodes if n.kind == NodeKind.BACKEND_ENDPOINT.value]
    frontend_ids = {n.id for n in frontend_nodes}
    backend_ids = {n.id for n in backend_nodes}
    calls_api_edges = [e for e in g.edges if e.kind == EdgeKind.CALLS_API.value]
    valid_calls_api_edges = [
        e for e in calls_api_edges if e.source in frontend_ids and e.target in backend_ids
    ]
    invalid_calls_api_edges = [
        e for e in calls_api_edges if not (e.source in frontend_ids and e.target in backend_ids)
    ]
    linked_frontend_ids = {e.source for e in valid_calls_api_edges}
    linked_backend_ids = {e.target for e in valid_calls_api_edges}
    unlinked_nodes = [n for n in frontend_nodes if n.id not in linked_frontend_ids]
    frontend_count = len(frontend_nodes)
    unlinked_count = len(unlinked_nodes)
    status = _api_link_status(
        frontend_count, len(backend_nodes), len(valid_calls_api_edges), unlinked_count)
    ratio = round(len(linked_frontend_ids) / frontend_count, 4) if frontend_count else 1.0
    return {
        "count": unlinked_count,
        "status": status,
        "diagnosis": _api_link_diagnosis(status),
        "frontend_api_calls": frontend_count,
        "backend_endpoints": len(backend_nodes),
        "calls_api_edges": len(valid_calls_api_edges),
        "raw_calls_api_edges": len(calls_api_edges),
        "invalid_calls_api_edges": len(invalid_calls_api_edges),
        "linked_frontend_api_calls": len(linked_frontend_ids),
        "linked_backend_endpoints": len(linked_backend_ids),
        "unlinked_frontend_api_calls": unlinked_count,
        "frontend_link_ratio": ratio,
        "unlinked_samples": [_api_node_sample(n) for n in unlinked_nodes[:_SAMPLE]],
        "backend_endpoint_samples": [_api_node_sample(n) for n in backend_nodes[:_SAMPLE]],
        "invalid_calls_api_samples": [
            {"source": e.source, "target": e.target, "kind": e.kind}
            for e in invalid_calls_api_edges[:_SAMPLE]
        ],
    }


def audit_graph(store: GraphStore, project_id: str, *,
                low_conf: float = _LOW_CONF) -> dict:
    """结构审计。返回分 errors/warnings 的报告 dict + clean 布尔(无 errors=clean)。

    后端探查部分(串台行 / 软产物 plugin 漂移)走 store.audit_scan —— load_graph 按 pid 过滤后
    看不见这些, 只有后端自查得到。canonical plugin 比对(域判定)留在本层, 不入 store。
    """
    g = store.load_graph(project_id)
    node_ids = {n.id for n in g.nodes}

    # --- errors ---
    dangling = []
    for e in g.edges:
        miss = [w for w, nid in (("source", e.source), ("target", e.target))
                if nid not in node_ids]
        if miss:
            dangling.append({"source": e.source, "target": e.target, "kind": e.kind,
                             "missing": miss})

    # cross-project + 软产物 plugin 漂移: load_graph 已按 pid 过滤看不见, 走后端探查。
    scan = store.audit_scan(project_id)
    cross_nodes = scan["foreign_project_ids"]["nodes"]
    cross_edges = scan["foreign_project_ids"]["edges"]

    # 软产物 plugin 漂移残留(2026-06-08 arch_layer 重复根因): 软节点/边只应来自规范 analyzer plugin。
    # store 回所有出现在软 kind 行上的 plugin, 本层滤掉 canonical = 孤儿(漂移残留)。
    canonical = _canonical_soft_plugin()
    orphan_node_plugins = sorted(p for p in scan["soft_plugins"]["nodes"] if p != canonical)
    orphan_edge_plugins = sorted(p for p in scan["soft_plugins"]["edges"] if p != canonical)

    # --- warnings ---
    by_key: dict[tuple, list[str]] = defaultdict(list)
    for n in g.nodes:
        if n.kind in _DUP_EXEMPT_KINDS:
            continue  # 同名列跨表合法, 不报重复
        by_key[(n.kind, (n.name or "").lower(), n.file or "")].append(n.id)
    dups = [{"kind": k[0], "name": k[1], "file": k[2], "ids": ids}
            for k, ids in by_key.items() if len(ids) > 1]

    low_edges = [e for e in g.edges
                 if not is_soft_edge_kind(e.kind) and (e.confidence if e.confidence is not None else 1.0) < low_conf]
    low_by_kind = Counter(e.kind for e in low_edges)

    # no-provenance: 硬边未盖 provenance 戳(src 来源不可追溯)。Phase 3 起按 pass 逐步补全
    # (calls/calls_api/contains-bridge 已盖; 插件直产的 reads_table/defines_api 等待后续),
    # 计数让补全进度可见。软边(LLM 派生)不计 —— 它们本就不进影响分析的确定结论。
    no_prov = [e for e in g.edges
               if not is_soft_edge_kind(e.kind) and not edge_provenance(e.meta)]
    no_prov_by_kind = Counter(e.kind for e in no_prov)

    # duplicate_edges: 同 (source,target,kind) 多行 = 多 plugin/parser 给同一关系(或退役 plugin
    # 残留, 如 builtin.codegraph_bridge vs call_resolvers), provenance/置信可能打架。impact 查询
    # 会**冲突消解**(保 provenance 更全者), audit 在此 surface 残留(提示该 purge 退役 plugin)。
    edge_copies = Counter((e.source, e.target, e.kind) for e in g.edges)
    dup_edges = [(k, c) for k, c in edge_copies.items() if c > 1]
    dup_edge_by_kind = Counter(k[2] for k, _ in dup_edges)

    orphan_plugins = sorted(set(orphan_node_plugins) | set(orphan_edge_plugins))
    errors = {
        "dangling_edges": {"count": len(dangling), "samples": dangling[:_SAMPLE]},
        "cross_project_nodes": {"count": len(cross_nodes), "foreign_project_ids": cross_nodes},
        "cross_project_edges": {"count": len(cross_edges), "foreign_project_ids": cross_edges},
        "orphan_soft_plugins": {
            "count": len(orphan_plugins), "plugins": orphan_plugins,
            "canonical": _canonical_soft_plugin(),
        },
    }
    warnings = {
        "duplicate_nodes": {"count": len(dups), "samples": dups[:_SAMPLE]},
        "low_confidence_edges": {
            "count": len(low_edges),
            "threshold": low_conf,
            "by_kind": dict(low_by_kind),
            "samples": [{"source": e.source, "target": e.target, "kind": e.kind,
                         "confidence": e.confidence} for e in low_edges[:_SAMPLE]],
        },
        "no_provenance_edges": {
            "count": len(no_prov),
            "by_kind": dict(no_prov_by_kind),
            "samples": [{"source": e.source, "target": e.target, "kind": e.kind}
                        for e in no_prov[:_SAMPLE]],
        },
        "duplicate_edges": {
            "count": len(dup_edges),
            "by_kind": dict(dup_edge_by_kind),
            "samples": [{"source": k[0], "target": k[1], "kind": k[2], "copies": c}
                        for k, c in dup_edges[:_SAMPLE]],
        },
        "api_link_coverage": _api_link_coverage(g),
    }
    n_errors = (errors["dangling_edges"]["count"]
                + errors["cross_project_nodes"]["count"]
                + errors["cross_project_edges"]["count"]
                + errors["orphan_soft_plugins"]["count"])
    return {
        "project_id": project_id,
        "totals": {
            "nodes": len(g.nodes), "edges": len(g.edges),
            "soft_nodes": sum(1 for n in g.nodes if is_soft_node_kind(n.kind)),
            "soft_edges": sum(1 for e in g.edges if is_soft_edge_kind(e.kind)),
        },
        "errors": errors,
        "warnings": warnings,
        "error_count": n_errors,
        "clean": n_errors == 0,
    }


def _unreadable_report(project_id: str, reason: str) -> dict:
    """读不动的 store 的占位报告: 全零结构 + audit_error 原因, 计 1 error(不崩门禁但可见)。

    形状与 audit_graph 返回完全一致(CLI 汇总行 / render_markdown 依赖这些键), 额外带
    audit_error 字段标注无法审计的原因。
    """
    return {
        "project_id": project_id,
        "totals": {"nodes": 0, "edges": 0, "soft_nodes": 0, "soft_edges": 0},
        "errors": {
            "dangling_edges": {"count": 0, "samples": []},
            "cross_project_nodes": {"count": 0, "foreign_project_ids": []},
            "cross_project_edges": {"count": 0, "foreign_project_ids": []},
            "orphan_soft_plugins": {"count": 0, "plugins": [],
                                    "canonical": _canonical_soft_plugin()},
        },
        "warnings": {
            "duplicate_nodes": {"count": 0, "samples": []},
            "low_confidence_edges": {"count": 0, "threshold": _LOW_CONF,
                                     "by_kind": {}, "samples": []},
            "no_provenance_edges": {"count": 0, "by_kind": {}, "samples": []},
            "duplicate_edges": {"count": 0, "by_kind": {}, "samples": []},
            "api_link_coverage": {
                "count": 0,
                "status": "unreadable",
                "diagnosis": "store 无法审计, 无法计算前后端 API 链路覆盖。",
                "frontend_api_calls": 0,
                "backend_endpoints": 0,
                "calls_api_edges": 0,
                "raw_calls_api_edges": 0,
                "invalid_calls_api_edges": 0,
                "linked_frontend_api_calls": 0,
                "linked_backend_endpoints": 0,
                "unlinked_frontend_api_calls": 0,
                "frontend_link_ratio": 0.0,
                "unlinked_samples": [],
                "backend_endpoint_samples": [],
                "invalid_calls_api_samples": [],
            },
        },
        "audit_error": reason,
        "error_count": 1,
        "clean": False,
    }


# pg 后端枚举时给 open_store 的占位 pid: 共享库构造只要 dsn 不用 pid(见 store.open_store)。
_AUDIT_PLACEHOLDER_PID = "__audit__"


def audit_all_stores(graph_store_dir=None) -> dict:
    """门禁聚合: 审计所有 project 的 graph store, 汇总结构 error。

    **只读门禁**(2026-06-09 audit #10): mode='ro' 打开, 不迁移 / 不改数据(schema 迁移是写侧
    ingest 职责)。读不动(旧 schema / 坏库)→ 记 1 error 不崩门禁。

    **按后端枚举**(旧版只 glob sqlite, 在 pg 后端会扫空 = 门禁形同虚设, 故分流):
    - 显式 graph_store_dir(测试 / 显式路径)或 sqlite 后端 → glob `<pid>.sqlite`(per-file 库)。
    - pg(共享库)后端 → 单 ro 连接 list_project_ids() 枚举全部 pid(无 .sqlite 文件)。

    fail-soft: pg 不可达 / 缺 dsn → 空报告 + 0 error(同 sqlite 无 store 的"优雅跳过", 不阻断 push)。
    返回 {projects: [pid...], reports: {pid: report}, total_errors: int}。
    """
    from pathlib import Path

    from codev_platform.core.paths import data_root

    # 显式 dir 走文件 glob(与 open_store"显式 path→sqlite"一致, 支持测试 tmp 隔离); 否则按后端分流。
    if graph_store_dir is not None:
        return _audit_sqlite_dir(Path(graph_store_dir))
    if _graph_backend() == "sqlite":
        return _audit_sqlite_dir(data_root() / "graph_store")
    return _audit_shared_backend()


def _audit_sqlite_dir(d) -> dict:
    """per-file sqlite 后端: glob `<pid>.sqlite`, 各开 ro 连接审计。无 store → 空 + 0。"""
    pids = sorted(p.stem for p in d.glob("*.sqlite")) if d.exists() else []
    reports: dict[str, dict] = {}
    total = 0
    for pid in pids:
        try:
            store = open_store(pid, mode="ro", path=d / f"{pid}.sqlite")
            try:
                rep = audit_graph(store, pid)
            finally:
                store.close()
        except GraphStoreUnreadable as exc:
            rep = _unreadable_report(
                pid, f"只读审计失败({exc}); 该 store 可能需 reindex 迁移到当前 schema")
        reports[pid] = rep
        total += rep["error_count"]
    return {"projects": pids, "reports": reports, "total_errors": total}


def _audit_shared_backend() -> dict:
    """共享后端(pg): 一个 ro 连接枚举共享库全部 project_id 逐个审计(复用同连接不 per-pid 重连)。
    连不上 / 缺 dsn → fail-soft 空报告(不崩门禁)。单 pid 读不动 → 记 1 error 继续其余。"""
    try:
        store = open_store(_AUDIT_PLACEHOLDER_PID, mode="ro")  # path=None → 按后端构造(pg)
    except (GraphStoreUnreadable, ValueError):
        return {"projects": [], "reports": {}, "total_errors": 0}
    reports: dict[str, dict] = {}
    total = 0
    try:
        try:
            pids = sorted(store.list_project_ids())
        except GraphStoreUnreadable:
            return {"projects": [], "reports": {}, "total_errors": 0}
        for pid in pids:
            try:
                rep = audit_graph(store, pid)
            except GraphStoreUnreadable as exc:
                rep = _unreadable_report(pid, f"只读审计失败({exc})")
            reports[pid] = rep
            total += rep["error_count"]
    finally:
        store.close()
    return {"projects": sorted(reports), "reports": reports, "total_errors": total}


def reconcile_orphan_pids(graph_pids, known_pids) -> dict:
    """孤儿 pid: graph 里有数据、但不在已登记 project 清单里的 pid(退役 project 残留库 / 串台)。

    纯集合差、无 IO: 调用方传入 graph 的 list_project_ids() 结果 + 登记表 pid 集 —— graph 层不依赖
    登记表来源(与 web/meta 解耦, 由 CLI 顶层组装)。返回 {orphan_pids:[...], count:int}。"""
    orphans = sorted(set(graph_pids) - set(known_pids))
    return {"orphan_pids": orphans, "count": len(orphans)}


def render_markdown(report: dict) -> str:
    """把审计报告渲染成可读 markdown。"""
    if report.get("audit_error"):
        # 读不动的 store: 全零结构无意义, 只报无法审计的原因(避免误读成 "0 问题 clean")。
        return (f"# graph audit — {report['project_id']}\n\n"
                f"- ❌ 无法审计: {report['audit_error']}")
    t = report["totals"]
    verdict = "✅ clean (无结构 error)" if report["clean"] else f"❌ {report['error_count']} 个 error"
    lines = [
        f"# graph audit — {report['project_id']}",
        "",
        f"- 节点 {t['nodes']}(软 {t['soft_nodes']}) / 边 {t['edges']}(软 {t['soft_edges']})",
        f"- 结论: {verdict}",
        "",
        "## errors (真 bug)",
    ]
    err = report["errors"]
    lines.append(f"- dangling edges(断链): {err['dangling_edges']['count']}")
    for s in err["dangling_edges"]["samples"]:
        lines.append(f"    - {s['source']} --{s['kind']}--> {s['target']} (缺 {','.join(s['missing'])})")
    lines.append(f"- cross-project 节点泄漏: {err['cross_project_nodes']['count']}"
                 + (f" (外来 pid: {err['cross_project_nodes']['foreign_project_ids']})"
                    if err['cross_project_nodes']['count'] else ""))
    lines.append(f"- cross-project 边泄漏: {err['cross_project_edges']['count']}")
    osp = err["orphan_soft_plugins"]
    lines.append(f"- 软产物孤儿 plugin(应只来自 {osp['canonical']}): {osp['count']}"
                 + (f" → {osp['plugins']} (plugin 漂移残留, 致重复/陈旧, 需 purge)"
                    if osp["count"] else ""))

    warn = report["warnings"]
    lines += ["", "## warnings (待 review)"]
    lines.append(f"- duplicate nodes(同 kind+name+file 多 id): {warn['duplicate_nodes']['count']}")
    for s in warn["duplicate_nodes"]["samples"]:
        lines.append(f"    - [{s['kind']}] {s['name']} @ {s['file']} → {len(s['ids'])} 个 id")
    lc = warn["low_confidence_edges"]
    lines.append(f"- low-confidence 硬边 (<{lc['threshold']}): {lc['count']} {dict(lc['by_kind'])}")
    npv = warn["no_provenance_edges"]
    lines.append(f"- no-provenance 硬边 (来源未盖戳, 逐步补全): {npv['count']} {dict(npv['by_kind'])}")
    dup = warn["duplicate_edges"]
    lines.append(f"- duplicate edges (同边多 plugin 重复/退役残留, impact 已消解): "
                 f"{dup['count']} {dict(dup['by_kind'])}")
    api = warn["api_link_coverage"]
    lines.append(
        f"- frontend API link coverage: {api['linked_frontend_api_calls']}/"
        f"{api['frontend_api_calls']} linked, backend endpoints {api['backend_endpoints']}, "
        f"calls_api {api['calls_api_edges']} ({api['status']})"
    )
    if api.get("invalid_calls_api_edges"):
        lines.append(f"    - invalid calls_api edges: {api['invalid_calls_api_edges']}")
    if api["count"]:
        lines.append(f"    - diagnosis: {api['diagnosis']}")
    for s in api["unlinked_samples"][:3]:
        lines.append(
            f"    - unlinked {s['http_method'] or '?'} {s['url'] or s['name']} @ {s['file']}"
        )
    return "\n".join(lines)
