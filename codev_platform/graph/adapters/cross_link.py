"""cross-link sqlite -> 统一 graph schema 适配器 (Phase 1 收尾).

把现有 `data/codegraph_ext/<project_id>/cross_layer.sqlite` 里的 nodes/edges
映射成 graph/schema.py 的 GraphNode/GraphEdge, 产出 AnalyzerResult。

设计原则:
- **不改 cross_link 现有代码与 schema** —— 只读消费。
- 优先复用现有只读连接 (cross_link.query.CrossLayerDB 用同一 sqlite),
  但适配器需要"全量节点/边"(query.py 是表中心 API, 不暴露所有节点),
  因此直接对同一个 sqlite 做只读 SELECT。
- **映射不丢信息**:cross-link 原始 kind / rel 无精确统一对应时,
  GraphNode.kind / GraphEdge.kind 保留裸字符串, 同时把原始值写进 meta
  (`cross_link_kind` / `cross_link_rel`), 任何映射都把原始值留底。
- 缺索引 (sqlite 不存在) 时返回空 AnalyzerResult, 不抛。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from codev_platform.core.paths import (
    cross_link_db_path,
    cross_link_legacy_db_path,
)
from codev_platform.graph.schema import (
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)

PLUGIN_NAME = "builtin.cross_link"

# cross-link 节点 kind -> 统一 NodeKind (无精确对应保留裸字符串, 原始 kind 进 meta)。
_NODE_KIND_MAP: dict[str, str] = {
    "table": NodeKind.DB_TABLE.value,
    "column": NodeKind.DB_COLUMN.value,
    "java_endpoint": NodeKind.BACKEND_ENDPOINT.value,
    "java_method": NodeKind.BACKEND_FUNCTION.value,
    "python_method": NodeKind.BACKEND_FUNCTION.value,
    "frontend_api": NodeKind.FRONTEND_API_CALL.value,
    "frontend_page": NodeKind.FRONTEND_ROUTE.value,
    "frontend_component": NodeKind.FRONTEND_COMPONENT.value,
    "flyway_migration": NodeKind.FILE.value,
}

# cross-link 边 rel -> 统一 EdgeKind (同理无对应保留裸字符串, 原始 rel 进 meta)。
_EDGE_KIND_MAP: dict[str, str] = {
    "queries_table": EdgeKind.READS_TABLE.value,
    "reads_table": EdgeKind.READS_TABLE.value,
    "writes_table": EdgeKind.WRITES_TABLE.value,
    "updates_table": EdgeKind.UPDATES_TABLE.value,
    "calls_api": EdgeKind.CALLS_API.value,
    "page_calls_api": EdgeKind.CALLS_API.value,
    # defines_table / defines_column 是 Flyway 定义关系, 统一 EdgeKind 暂无精确
    # 对应 -> 保留裸字符串, 原始 rel 仍写进 meta 留底。
    "defines_table": "defines_table",
    "defines_column": "defines_column",
}


def _map_node_kind(raw_kind: str) -> tuple[str, dict[str, Any]]:
    """返回 (统一 kind, 需并入 meta 的原始信息)。"""
    mapped = _NODE_KIND_MAP.get(raw_kind)
    if mapped is None:
        # 无映射 -> 保留裸字符串 kind
        return raw_kind, {"cross_link_kind": raw_kind}
    return mapped, {"cross_link_kind": raw_kind}


def _map_edge_kind(raw_rel: str) -> tuple[str, dict[str, Any]]:
    mapped = _EDGE_KIND_MAP.get(raw_rel)
    if mapped is None:
        return raw_rel, {"cross_link_rel": raw_rel}
    return mapped, {"cross_link_rel": raw_rel}


def _node_id(project_id: str, raw_kind: str, db_id: int) -> str:
    """统一稳定 id: "<project_id>:<cross_link_kind>:<db_rowid>" (跨插件可链接)。"""
    return f"{project_id}:{raw_kind}:{db_id}"


def _open_readonly(project_id: str) -> sqlite3.Connection | None:
    """打开 per-project cross-link sqlite (只读)。缺库返回 None (不抛)。

    路径解析与 cross_link.schema.open_db 一致: 新路径优先, 否则 legacy fallback。
    不存在任何库 -> None。
    """
    new_path = cross_link_db_path(project_id)
    legacy_path = cross_link_legacy_db_path()
    if new_path.exists():
        target = new_path
    elif legacy_path.exists():
        target = legacy_path
    else:
        return None
    # 只读打开 (uri mode=ro), 不创建 / 不写, 绝不触碰 cross_link 写路径
    conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def build_cross_link_result(project_id: str) -> AnalyzerResult:
    """读该 project 的 cross-link sqlite, 映射成统一 AnalyzerResult。

    缺索引时返回空 AnalyzerResult (nodes/edges 皆空), 不抛异常。
    """
    result = AnalyzerResult(plugin=PLUGIN_NAME)
    conn = _open_readonly(project_id)
    if conn is None:
        return result
    try:
        # db rowid -> 统一 node id, 用于边端点解析
        rowid_to_id: dict[int, str] = {}

        for row in conn.execute(
            "SELECT id, kind, name, path, line, language, meta_json FROM nodes"
        ):
            raw_kind = row["kind"]
            unified_kind, kind_meta = _map_node_kind(raw_kind)
            meta: dict[str, Any] = {}
            if row["meta_json"]:
                try:
                    parsed = json.loads(row["meta_json"])
                    if isinstance(parsed, dict):
                        meta.update(parsed)
                except (ValueError, TypeError):
                    meta["raw_meta_json"] = row["meta_json"]
            meta.update(kind_meta)
            node_id = _node_id(project_id, raw_kind, int(row["id"]))
            rowid_to_id[int(row["id"])] = node_id
            result.nodes.append(
                GraphNode(
                    id=node_id,
                    kind=unified_kind,
                    name=row["name"],
                    project_id=project_id,
                    file=row["path"],
                    line=row["line"],
                    language=row["language"],
                    meta=meta,
                )
            )

        for row in conn.execute(
            "SELECT src_id, rel, dst_id, confidence, evidence FROM edges"
        ):
            src = rowid_to_id.get(int(row["src_id"]))
            dst = rowid_to_id.get(int(row["dst_id"]))
            if src is None or dst is None:
                # 悬挂边 (端点节点缺失) -> 跳过, 不构造无效边
                continue
            unified_rel, rel_meta = _map_edge_kind(row["rel"])
            meta = dict(rel_meta)
            if row["evidence"]:
                meta["evidence"] = row["evidence"]
            confidence = (
                float(row["confidence"]) if row["confidence"] is not None else 1.0
            )
            result.edges.append(
                GraphEdge(
                    source=src,
                    target=dst,
                    kind=unified_rel,
                    confidence=confidence,
                    meta=meta,
                )
            )
    finally:
        conn.close()

    return result
