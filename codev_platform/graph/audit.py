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

import sqlite3
from collections import Counter, defaultdict

from codev_platform.graph.schema import (
    SOFT_EDGE_KINDS,
    SOFT_NODE_KINDS,
    NodeKind,
    edge_provenance,
    is_soft_edge_kind,
    is_soft_node_kind,
)
from codev_platform.graph.store import load_graph, open_store

_LOW_CONF = 0.7
_SAMPLE = 10
# 这些 kind 的 name 在同一 file 内**合法重复**, 不当"重复节点"报:
# db_column —— 同一文件多张表常有同名列 (id / meta_json / plugin ...), 各属不同表非重复。
_DUP_EXEMPT_KINDS = frozenset({NodeKind.DB_COLUMN.value})


def _distinct_project_ids(conn: sqlite3.Connection, table: str) -> list[str]:
    try:
        return [r[0] for r in conn.execute(f"SELECT DISTINCT project_id FROM {table}")]
    except sqlite3.Error:
        return []


def _canonical_soft_plugin() -> str:
    """软产物(ARCH_LAYER/BUSINESS_DOMAIN...)唯一合法来源 plugin(见 ingest._analyzers_pass)。"""
    try:
        from codev_platform.graph.ingest import ANALYZERS_PLUGIN
        return ANALYZERS_PLUGIN
    except Exception:  # noqa: BLE001
        return "builtin.analyzers"


def _orphan_soft_plugins(conn: sqlite3.Connection, project_id: str,
                         kinds: frozenset[str], table: str) -> list[str]:
    """软 kind 的行里, plugin != 规范 analyzer plugin 的 = 孤儿(plugin 漂移残留, 致重复/陈旧)。

    这正是 2026-06-08 抓到的 arch_layer 重复根因: analyzer 自名 plugin 直 upsert 的残留,
    reindex 只清规范 plugin 故永不被清。soft 节点/边**只应**来自 _analyzers_pass 的统一 plugin。
    """
    if not kinds:
        return []
    canonical = _canonical_soft_plugin()
    ph = ",".join("?" for _ in kinds)
    try:
        rows = conn.execute(
            f"SELECT DISTINCT plugin FROM {table} WHERE project_id = ? AND kind IN ({ph})",
            (project_id, *kinds),
        ).fetchall()
    except sqlite3.Error:
        return []
    return sorted({r[0] for r in rows if r[0] != canonical})


def audit_graph(conn: sqlite3.Connection, project_id: str, *,
                low_conf: float = _LOW_CONF) -> dict:
    """结构审计。返回分 errors/warnings 的报告 dict + clean 布尔(无 errors=clean)。"""
    g = load_graph(conn, project_id)
    node_ids = {n.id for n in g.nodes}

    # --- errors ---
    dangling = []
    for e in g.edges:
        miss = [w for w, nid in (("source", e.source), ("target", e.target))
                if nid not in node_ids]
        if miss:
            dangling.append({"source": e.source, "target": e.target, "kind": e.kind,
                             "missing": miss})

    # cross-project: per-project 库里不该有别 project 的行 (load_graph 已按 pid 过滤,
    # 故必须直接查原表才能发现串台)。
    cross_nodes = [p for p in _distinct_project_ids(conn, "nodes") if p != project_id]
    cross_edges = [p for p in _distinct_project_ids(conn, "edges") if p != project_id]

    # 软产物 plugin 漂移残留(2026-06-08 arch_layer 重复根因): 软节点/边只应来自规范 analyzer plugin。
    orphan_node_plugins = _orphan_soft_plugins(conn, project_id, SOFT_NODE_KINDS, "nodes")
    orphan_edge_plugins = _orphan_soft_plugins(conn, project_id, SOFT_EDGE_KINDS, "edges")

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


def audit_all_stores(graph_store_dir) -> dict:
    """门禁聚合: 审计某目录下所有 `<pid>.sqlite` graph store, 汇总结构 error。

    返回 {projects: [pid...], reports: {pid: report}, total_errors: int}。
    目录不存在 / 无 store → projects 空 + total_errors 0(调用方据此优雅跳过, 不阻断)。
    pre-push / CI 门禁用: total_errors>0 即应非零退出。
    """
    from pathlib import Path

    d = Path(graph_store_dir)
    pids = sorted(p.stem for p in d.glob("*.sqlite")) if d.exists() else []
    reports: dict[str, dict] = {}
    total = 0
    for pid in pids:
        conn = open_store(pid, path=d / f"{pid}.sqlite")
        try:
            rep = audit_graph(conn, pid)
        finally:
            conn.close()
        reports[pid] = rep
        total += rep["error_count"]
    return {"projects": pids, "reports": reports, "total_errors": total}


def render_markdown(report: dict) -> str:
    """把审计报告渲染成可读 markdown。"""
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
    return "\n".join(lines)
