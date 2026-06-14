"""builtin.sql._common — 跨扫描域共享的 helper / 常量 / 节点出口。

按职责拆分 sql 包后, ddl / orm / core 三大扫描域共用的小工具 (引号剥离 / ast 取值 /
表名合理性 / 节点边出口 / 测试路径过滤) 集中在此, 单一真值源不重复实现。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from codev_platform.graph.schema import (
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)


def _read_text(path: Path) -> str:
    """读源文件, 容忍非 UTF-8 (遗留仓常有 GBK/Latin1 的 .sql/.hbm.xml/.java)。

    utf-8 优先 (快路径); 解码失败回退 errors="ignore" —— 丢坏字节但保住 CREATE TABLE /
    <class table=> 等结构 (表/列名是 ASCII, 不受影响)。否则**一个**非 utf-8 文件就让整个
    SqlPlugin.analyze 抛 UnicodeDecodeError, ingest fail-soft 丢掉全插件 → 该仓 DB 层全黑
    (2026-06-14 ideas-v2 实证: 一个 GBK .sql 让 1068 个 hbm 表全扫不到)。OSError 仍由调用方处理。
    """
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")

# DB 定义关系边 kind: 统一 EdgeKind 暂无精确枚举对应, 用裸字符串 defines_column
# (前端 unifiedgraph / codegraph utils 真消费此边 kind, 渲染"定义字段"关系)。
_REL_DEFINES_COLUMN = "defines_column"


def _unquote(name: str) -> str:
    """去掉表名 / 列名外层引号 (反引号 / 双引号 / 方括号) 并取末段 (剥 schema 前缀)。"""
    name = name.strip().strip("`\"[]")
    if "." in name:
        name = name.split(".")[-1].strip("`\"[]")
    return name


def _str_const(node: ast.expr) -> str | None:
    """取 ast 字符串常量值, 非字符串常量返回 None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _call_attr_chain(call: ast.Call) -> str | None:
    """取 Call 的被调名最后一段 (Column / models.CharField -> 'Column' / 'CharField')。"""
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


# FROM 后非真实表的 token (子查询别名 / 关键字)。
_NON_TABLE = frozenset({"select", "dual", "where", "set", "values", "table", "only", "lateral"})


def _plausible_table(t: str) -> bool:
    return bool(t) and t not in _NON_TABLE and not t.isdigit() and len(t) >= 2


def _emit_table_nodes(
    table: str,
    columns: list[tuple[str, str | None]],
    *,
    rel: str,
    line: int,
    language: str,
    project_id: str,
    table_meta: dict,
    col_meta: dict,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """统一产 db_table + db_column 节点 + defines_column 边 (三种源共用的出口)。

    columns: (col_name, col_type|None) 列表。语义/id 形态与 .sql 路径完全一致,
    保证跨源 (sql / python-ddl / sqlalchemy / django) 同 table/column id 可去重 + 链接。
    """
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    table_key = table.lower()
    table_id = f"{project_id}:db_table:{table_key}"
    nodes.append(
        GraphNode(
            id=table_id,
            kind=NodeKind.DB_TABLE.value,
            name=table,
            project_id=project_id,
            file=rel,
            line=line,
            language=language,
            meta=dict(table_meta),
        )
    )
    seen_cols: set[str] = set()
    for col_name, col_type in columns:
        if not col_name:
            continue
        col_key = col_name.lower()
        if col_key in seen_cols:  # 同表同列名 (源内重复) 去重。
            continue
        seen_cols.add(col_key)
        col_id = f"{project_id}:db_column:{table_key}.{col_key}"
        meta = dict(col_meta)
        meta["table"] = table
        if col_type is not None:
            meta["col_type"] = col_type
        nodes.append(
            GraphNode(
                id=col_id,
                kind=NodeKind.DB_COLUMN.value,
                name=col_name,
                project_id=project_id,
                file=rel,
                line=line,
                language=language,
                meta=meta,
            )
        )
        edges.append(
            GraphEdge(
                source=table_id,
                target=col_id,
                kind=_REL_DEFINES_COLUMN,
                meta={
                    "evidence": f"column {col_name} of {table}",
                },
            )
        )
    return nodes, edges


def _emit_table_access(
    func_id: str,
    writes: set[str],
    reads: set[str],
    project_id: str,
    known_tables: set[str],
    seen_stub: set[str],
    confidence: float = 1.0,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """给定一个函数 + 它读写的表集, 产 reads/writes_table 边 (+ 未定义表的 inferred stub)。

    Python / Java DML / MyBatis-Plus 扫描共用 (单一真值源, 不重复实现表访问边逻辑)。
    confidence < 1.0 用于粗粒度推断 (如 BaseMapper CRUD 未细分读/写)。
    """
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    access = (
        [(t, EdgeKind.WRITES_TABLE.value) for t in sorted(writes)]
        + [(t, EdgeKind.READS_TABLE.value) for t in sorted(reads)]
    )
    for table, edge_kind in access:
        table_id = f"{project_id}:db_table:{table}"
        if table not in known_tables and table not in seen_stub:
            seen_stub.add(table)
            nodes.append(
                GraphNode(
                    id=table_id,
                    kind=NodeKind.DB_TABLE.value,
                    name=table,
                    project_id=project_id,
                    meta={"source": "dml-inferred", "inferred": True},
                )
            )
        edges.append(
            GraphEdge(
                source=func_id,
                target=table_id,
                kind=edge_kind,
                confidence=confidence,
                meta={"evidence": f"{edge_kind} {table}"},
            )
        )
    return nodes, edges


# 测试夹具路径: tests/ 目录 / test_*.py / *_test.py / conftest.py。这些文件里的
# CREATE TABLE / Table() / DML 是测试夹具, 不是生产 schema, 扫进图谱会造幽灵表/幽灵 reader。
# 仅在 sql 插件**表定义/访问扫描**层过滤 (不动全局 _stack_scan._SKIP_DIRS —— 那是目录剪枝,
# 改它会让别的插件也看不到测试代码)。
_RE_TEST_PATH = re.compile(
    r"(^|/)tests?/|(^|/)test_[^/]*\.py$|(^|/)[^/]*_test\.py$|(^|/)conftest\.py$"
)


def _is_test_path(rel: str) -> bool:
    return bool(_RE_TEST_PATH.search(rel))
