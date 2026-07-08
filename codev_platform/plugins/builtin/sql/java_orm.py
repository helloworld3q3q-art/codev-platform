"""Java ORM / dao-service querymodel 扫描域。

覆盖两类确定性结构:
- JPA `@Entity` + `@Table` + `@Column` -> db_table / db_column。
- dao-service `Q<Entity>.<root>.select()/selectCount()` -> backend_function reads_table。

本模块只做 Java ORM 家族扫描, 不碰 ingest / call_resolvers; SQL 插件负责统一编排。
"""
from __future__ import annotations

import re

from codev_platform.graph.schema import GraphEdge, GraphNode, NodeKind
from codev_platform.plugins.builtin.sql._common import _emit_table_access, _emit_table_nodes

_RE_ENTITY = re.compile(r"@Entity\b", re.ASCII)
_RE_TABLE_ANN = re.compile(r"@Table\b", re.ASCII)
_RE_CLASS_DECL = re.compile(r"\bclass\s+(?P<name>\w+)", re.ASCII)
_RE_COLUMN_ANN = re.compile(r"@Column\b", re.ASCII)
_RE_FIELD_DECL = re.compile(
    r"\b(?:private|protected|public)\s+"
    r"(?:static\s+|final\s+|transient\s+|volatile\s+)*"
    r"(?P<type>[\w<>.?]+)\s+(?P<name>\w+)\s*(?:=|;)",
    re.ASCII,
)
_RE_QMODEL_DECL = re.compile(
    r"\bclass\s+(?P<qclass>Q\w+)\s+extends\s+BaseModelExpression\s*<\s*(?P<entity>\w+)",
    re.DOTALL | re.ASCII,
)
_RE_QMODEL_ROOT = re.compile(
    r"BaseModelExpression\s*<[^;=]+>\s+(?P<root>\w+)\s*=\s*new\s+(?P<qclass>Q\w+)\s*\(",
    re.DOTALL | re.ASCII,
)
_RE_METHOD_DECL = re.compile(
    r"(?m)^[ \t]*(?:public|private|protected)\s+"
    r"(?:static\s+|final\s+|synchronized\s+)*"
    r"[\w<>\[\],.?]+\s+(?P<name>\w+)\s*\([^;{}]*\)\s*(?:throws\s+[^{]+)?\{",
    re.ASCII,
)
_RE_DAO_QUERY = re.compile(
    r"\b(?P<qclass>Q\w+)\s*\.\s*(?P<root>\w+)\s*\.\s*"
    r"(?P<op>select|selectCount)\s*\(",
    re.DOTALL | re.ASCII,
)


def is_java_jpa_or_querymodel(text: str) -> bool:
    """廉价 detect 信号:标准 JPA 实体或 dao-service querymodel。"""
    return ("@Entity" in text and "@Table" in text) or "BaseModelExpression" in text


def _is_escaped(text: str, idx: int) -> bool:
    slashes = 0
    i = idx - 1
    while i >= 0 and text[i] == "\\":
        slashes += 1
        i -= 1
    return slashes % 2 == 1


def _matching_pair(text: str, open_idx: int, open_ch: str, close_ch: str) -> int:
    depth = 0
    quote: str | None = None
    for i in range(open_idx, len(text)):
        ch = text[i]
        if quote is not None:
            if ch == quote and not _is_escaped(text, i):
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
    return len(text)


def _matching_brace(text: str, open_idx: int) -> int:
    return _matching_pair(text, open_idx, "{", "}")


def _annotation_args(text: str, ann: re.Match[str]) -> tuple[str, int] | None:
    """返回注解括号内参数与注解结束位置; 无括号注解返回空参数。"""
    idx = ann.end()
    while idx < len(text) and text[idx].isspace():
        idx += 1
    if idx >= len(text) or text[idx] != "(":
        return "", ann.end()
    close_idx = _matching_pair(text, idx, "(", ")")
    if close_idx >= len(text):
        return None
    return text[idx + 1:close_idx], close_idx + 1


def _split_top_level_args(args: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    for i, ch in enumerate(args):
        if quote is not None:
            if ch == quote and not _is_escaped(args, i):
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
        elif ch in "({[":
            depth += 1
        elif ch in ")}]" and depth > 0:
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(args[start:i].strip())
            start = i + 1
    tail = args[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _ann_string_arg(args: str, key: str = "name", *, allow_positional: bool = False) -> str | None:
    for part in _split_top_level_args(args):
        keyed = re.match(rf"{re.escape(key)}\s*=\s*\"([^\"]+)\"\s*$", part, re.ASCII)
        if keyed:
            return keyed.group(1)
    if allow_positional:
        first = re.match(r"\"([^\"]+)\"\s*$", args.strip(), re.ASCII)
        return first.group(1) if first else None
    return None


def _class_body(text: str, decl: re.Match[str]) -> str:
    open_idx = text.find("{", decl.end())
    if open_idx < 0:
        return ""
    return text[open_idx + 1:_matching_brace(text, open_idx)]


def _jpa_columns(body: str) -> list[tuple[str, str | None]]:
    cols: list[tuple[str, str | None]] = []
    for ann in _RE_COLUMN_ANN.finditer(body):
        parsed = _annotation_args(body, ann)
        if parsed is None:
            continue
        args, ann_end = parsed
        col = _ann_string_arg(args)
        semi = body.find(";", ann_end)
        if semi < 0:
            continue
        window = body[ann_end:semi + 1]
        field = _RE_FIELD_DECL.search(window)
        if not col and field:
            col = field.group("name")
        if col:
            cols.append((col, field.group("type") if field else None))
    return cols


def _scan_java_jpa_entities(
    java_srcs: list[tuple[str, str]], project_id: str
) -> tuple[list[GraphNode], list[GraphEdge], dict[str, str]]:
    """扫 Java JPA entity -> 表/列节点, 返回 entity 简名到表名 lower 的映射。"""
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    entity_table: dict[str, str] = {}
    for rel, src in java_srcs:
        for ent in _RE_ENTITY.finditer(src):
            decl = _RE_CLASS_DECL.search(src, ent.end())
            if decl is None:
                continue
            header = src[ent.start():decl.start()]
            table = None
            for table_m in _RE_TABLE_ANN.finditer(header):
                parsed = _annotation_args(header, table_m)
                if parsed is None:
                    continue
                table = _ann_string_arg(parsed[0])
                if table:
                    break
            if not table:
                continue
            entity = decl.group("name")
            entity_table[entity.lower()] = table.lower()
            line = src.count("\n", 0, ent.start()) + 1
            ns, es = _emit_table_nodes(
                table,
                _jpa_columns(_class_body(src, decl)),
                rel=rel,
                line=line,
                language="java",
                project_id=project_id,
                table_meta={"source": "java-jpa", "entity_class": entity},
                col_meta={"source": "java-jpa"},
            )
            nodes.extend(ns)
            edges.extend(es)
    return nodes, edges, entity_table


def _querymodel_roots(
    java_srcs: list[tuple[str, str]], entity_to_table: dict[str, str]
) -> dict[tuple[str, str], str]:
    """返回 {(QClass, rootField): table_lower}。只映射已知实体表, 避免猜表。"""
    qclass_entity: dict[str, str] = {}
    roots: dict[tuple[str, str], str] = {}
    for _rel, src in java_srcs:
        for m in _RE_QMODEL_DECL.finditer(src):
            entity = m.group("entity").lower()
            if entity in entity_to_table:
                qclass_entity[m.group("qclass")] = entity
        for m in _RE_QMODEL_ROOT.finditer(src):
            qclass = m.group("qclass")
            entity = qclass_entity.get(qclass)
            if entity:
                roots[(qclass, m.group("root"))] = entity_to_table[entity]
    return roots


def _java_methods(src: str) -> list[tuple[str, int, str]]:
    methods: list[tuple[str, int, str]] = []
    for m in _RE_METHOD_DECL.finditer(src):
        open_idx = src.find("{", m.end() - 1)
        if open_idx < 0:
            continue
        close_idx = _matching_brace(src, open_idx)
        methods.append((
            m.group("name"),
            src.count("\n", 0, m.start()) + 1,
            src[open_idx + 1:close_idx],
        ))
    return methods


def _scan_dao_service_queries(
    java_srcs: list[tuple[str, str]],
    project_id: str,
    known_tables: set[str],
    entity_to_table: dict[str, str],
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """扫 dao-service querymodel select/selectCount -> backend_function reads_table。"""
    roots = _querymodel_roots(java_srcs, entity_to_table)
    if not roots:
        return [], []
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    seen_func: set[str] = set()
    seen_stub: set[str] = set()
    for rel, src in java_srcs:
        for method, line, body in _java_methods(src):
            reads = {
                roots[(m.group("qclass"), m.group("root"))]
                for m in _RE_DAO_QUERY.finditer(body)
                if (m.group("qclass"), m.group("root")) in roots
            }
            if not reads:
                continue
            func_id = f"{project_id}:backend_function:{rel}:{method}"
            if func_id not in seen_func:
                seen_func.add(func_id)
                nodes.append(
                    GraphNode(
                        id=func_id,
                        kind=NodeKind.BACKEND_FUNCTION.value,
                        name=method,
                        project_id=project_id,
                        file=rel,
                        line=line,
                        language="java",
                        meta={"db_access": True, "dao_service": True},
                    )
                )
            ns, es = _emit_table_access(
                func_id, set(), reads, project_id, known_tables, seen_stub, confidence=0.9
            )
            nodes.extend(ns)
            edges.extend(es)
    return nodes, edges
