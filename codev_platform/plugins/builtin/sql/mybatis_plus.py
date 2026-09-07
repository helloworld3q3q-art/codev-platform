"""MyBatis-Plus BaseMapper CRUD scanner."""
from __future__ import annotations

import re

from codev_platform.graph.schema import GraphEdge, GraphNode, NodeKind
from codev_platform.plugins.builtin.sql._common import (
    _emit_table_access,
    _plausible_table,
    _unquote,
)


_RE_TABLENAME = re.compile(r'@TableName\s*\(\s*"([^"]+)"', re.ASCII)
_RE_ENTITY_CLASS = re.compile(r"\bclass\s+(\w+)", re.ASCII)
_RE_BASEMAPPER = re.compile(
    r"\binterface\s+(?P<mapper>\w+)\b[^{]*?extends\s+\w*BaseMapper\s*<\s*(?P<entity>\w+)",
    re.ASCII | re.S,
)
_MYBATIS_PLUS_CONF = 0.6


def _scan_entity_tables(java_srcs: list[tuple[str, str]]) -> dict[str, str]:
    """Build {entity class -> table name} from @TableName annotations."""
    out: dict[str, str] = {}
    for _rel, src in java_srcs:
        for m in _RE_TABLENAME.finditer(src):
            table = m.group(1).strip()
            cm = _RE_ENTITY_CLASS.search(src, m.end())
            if cm and table:
                out[cm.group(1)] = table
    return out


def _scan_mybatis_plus(
    java_srcs: list[tuple[str, str]],
    project_id: str,
    known_tables: set[str],
    entity_table: dict[str, str],
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """BaseMapper<Entity> -> @TableName table; emit coarse read/write table edges."""
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    seen_func: set[str] = set()
    seen_stub: set[str] = set()
    for rel, src in java_srcs:
        for m in _RE_BASEMAPPER.finditer(src):
            mapper = m.group("mapper")
            entity = m.group("entity")
            table = entity_table.get(entity)
            if not table:
                continue
            t = _unquote(table).lower()
            if not _plausible_table(t):
                continue
            line = src.count("\n", 0, m.start()) + 1
            func_id = f"{project_id}:backend_function:{rel}:{mapper}"
            if func_id not in seen_func:
                seen_func.add(func_id)
                nodes.append(
                    GraphNode(
                        id=func_id,
                        kind=NodeKind.BACKEND_FUNCTION.value,
                        name=mapper,
                        project_id=project_id,
                        file=rel,
                        line=line,
                        language="java",
                        meta={"db_access": True, "mybatis_plus": True, "entity": entity},
                    )
                )
            a_nodes, a_edges = _emit_table_access(
                func_id, {t}, {t}, project_id, known_tables, seen_stub,
                confidence=_MYBATIS_PLUS_CONF,
            )
            nodes.extend(a_nodes)
            edges.extend(a_edges)
    return nodes, edges
