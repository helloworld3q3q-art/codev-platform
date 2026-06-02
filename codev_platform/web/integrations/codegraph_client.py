"""codegraph 只读 SQLite 客户端 (plan §12.1 资源隔离铁律 + §二十)。

吸收 Java codegraph-api 的 6 个只读接口 (stats/search/node/neighbors/file-tree/graph),
直读 per-project `codegraph.db`, **不经任何 daemon / 不起模型** → 零 GPU、零写锁争用。

SQL 逐句对齐 Java CodeGraphMapper + 装配逻辑对齐 CodeGraphFacadeService, 保证字段形状一致
(前端 force-graph 从 :18082 平滑迁移)。异常一律转 PlatformError:
- DB 文件缺失 → INDEX_MISSING (503)
- 入参非法    → INVALID_PARAMS (400)
- sqlite 故障 → UPSTREAM_UNAVAILABLE (503)

连接以只读模式打开 (file: URI + mode=ro), 多会话天然并发, 绝不写库。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from codev_platform.core.errors import ErrorCode, PlatformError
from codev_platform.core.paths import codegraph_db_path

_DEFAULT_SEARCH_LIMIT = 50
_MAX_SEARCH_LIMIT = 500
_DEFAULT_GRAPH_LIMIT = 2000
_MAX_GRAPH_LIMIT = 20000
_MAX_EDGE_LIMIT = 50000

# nodes 列 -> camelCase 别名, 对齐 Java mapper (start_line as "startLine" 等)。
_NODE_COLS = (
    'id, kind, name, qualified_name as "qualifiedName", file_path as "filePath", language, '
    'start_line as "startLine", end_line as "endLine", '
    'start_column as "startColumn", end_column as "endColumn", '
    'docstring, signature, visibility, '
    'is_exported as "isExported", is_async as "isAsync", '
    'is_static as "isStatic", is_abstract as "isAbstract"'
)


def _db_path(project_id: str | None, db_path: str | Path | None) -> Path:
    """显式 db_path (测试注入) 优先, 否则按 project_id 解析平台 codegraph.db。"""
    if db_path is not None:
        return Path(db_path)
    if not project_id:
        raise PlatformError(ErrorCode.INVALID_PARAMS, "project_id is required for codegraph query")
    return codegraph_db_path(project_id)


def _connect_ro(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise PlatformError(
            ErrorCode.INDEX_MISSING,
            "codegraph index not found for this project.",
            detail=f"missing: {path}",
        )
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as exc:  # noqa: BLE001
        raise PlatformError(
            ErrorCode.UPSTREAM_UNAVAILABLE, "codegraph database unavailable.", detail=str(exc)
        ) from exc


def _bool(v) -> bool | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return int(v) != 0
    s = str(v).strip()
    return s == "1" or s.lower() == "true"


def _node_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("isExported", "isAsync", "isStatic", "isAbstract"):
        if k in d:
            d[k] = _bool(d[k])
    return d


def _clamp(value: int | None, default: int, maximum: int) -> int:
    if value is None or value <= 0:
        return default
    return min(value, maximum)


def _norm(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    out = [v.strip() for v in values if v and v.strip()]
    return out or None


def _in_clause(column: str, values: list[str] | None) -> tuple[str, list]:
    if not values:
        return "", []
    placeholders = ",".join("?" for _ in values)
    return f" AND {column} IN ({placeholders})", list(values)


class CodegraphClient:
    """per-request 只读客户端。用 with 语句管理连接生命周期。"""

    def __init__(self, project_id: str | None = None, *, db_path: str | Path | None = None) -> None:
        self._path = _db_path(project_id, db_path)
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> "CodegraphClient":
        self._conn = _connect_ro(self._path)
        return self

    def __exit__(self, *exc) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise PlatformError(ErrorCode.INTERNAL, "codegraph client not opened")
        return self._conn

    # ----------------------------- stats -----------------------------

    def stats(self) -> dict:
        c = self.conn
        total_files = c.execute("select count(*) from files").fetchone()[0]
        total_nodes = c.execute("select count(*) from nodes").fetchone()[0]
        total_edges = c.execute("select count(*) from edges").fetchone()[0]
        by_lang = {r[0]: r[1] for r in c.execute(
            "select language as k, count(*) as v from nodes group by language") if r[0] is not None}
        by_node = {r[0]: r[1] for r in c.execute(
            "select kind as k, count(*) as v from nodes group by kind") if r[0] is not None}
        by_edge = {r[0]: r[1] for r in c.execute(
            "select kind as k, count(*) as v from edges group by kind") if r[0] is not None}
        return {
            "totalFiles": total_files, "totalNodes": total_nodes, "totalEdges": total_edges,
            "byLanguage": by_lang, "byNodeKind": by_node, "byEdgeKind": by_edge,
        }

    # ----------------------------- search ----------------------------

    def search(self, keyword: str | None, languages: list[str] | None,
               kinds: list[str] | None, limit: int | None) -> list[dict]:
        kw = (keyword or "").strip()
        if not kw:
            raise PlatformError(ErrorCode.INVALID_PARAMS, "keyword 不能为空")
        lim = _clamp(limit, _DEFAULT_SEARCH_LIMIT, _MAX_SEARCH_LIMIT)
        fts_query = self._build_fts_prefix(kw)
        # nodes_fts join nodes: 列加 n. 前缀避免歧义, camelCase 别名对齐 Java mapper。
        sql = (
            "select n.id, n.kind, n.name, n.qualified_name as \"qualifiedName\", "
            "n.file_path as \"filePath\", n.language, "
            "n.start_line as \"startLine\", n.end_line as \"endLine\", "
            "n.start_column as \"startColumn\", n.end_column as \"endColumn\", "
            "n.docstring, n.signature, n.visibility, "
            "n.is_exported as \"isExported\", n.is_async as \"isAsync\", "
            "n.is_static as \"isStatic\", n.is_abstract as \"isAbstract\" "
            "from nodes_fts fts join nodes n on n.rowid = fts.rowid "
            "where nodes_fts match ?"
        )
        params: list = [fts_query]
        lang_clause, lang_p = _in_clause("n.language", _norm(languages))
        kind_clause, kind_p = _in_clause("n.kind", _norm(kinds))
        sql += lang_clause + kind_clause + " order by bm25(nodes_fts) limit ?"
        params += lang_p + kind_p + [lim]
        try:
            rows = self.conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:  # noqa: BLE001 — FTS5 语法/缺失等
            raise PlatformError(ErrorCode.UPSTREAM_UNAVAILABLE,
                                "codegraph search failed.", detail=str(exc)) from exc
        return [_node_dict(r) for r in rows]

    @staticmethod
    def _build_fts_prefix(kw: str) -> str:
        parts = []
        for t in kw.split():
            if not t:
                continue
            escaped = t.replace('"', '""')
            parts.append(f'"{escaped}"*')
        return " ".join(parts) if parts else '""'

    # ----------------------------- node ------------------------------

    def node(self, node_id: str | None) -> dict | None:
        if not node_id:
            raise PlatformError(ErrorCode.INVALID_PARAMS, "id 不能为空")
        r = self.conn.execute(f"select {_NODE_COLS} from nodes where id = ?", (node_id,)).fetchone()
        return _node_dict(r) if r is not None else None

    # --------------------------- neighbors ---------------------------

    def neighbors(self, node_id: str | None, direction: str | None,
                  edge_kinds: list[str] | None) -> dict:
        if not node_id:
            raise PlatformError(ErrorCode.INVALID_PARAMS, "id 不能为空")
        d = (direction or "both").strip().lower() or "both"
        if d not in ("in", "out", "both"):
            raise PlatformError(ErrorCode.INVALID_PARAMS, "direction 必须是 in / out / both")
        c = self.conn
        center_row = c.execute(f"select {_NODE_COLS} from nodes where id = ?", (node_id,)).fetchone()
        center = _node_dict(center_row) if center_row is not None else None

        if d == "in":
            where = "target = ?"
        elif d == "out":
            where = "source = ?"
        else:
            where = "(source = ? or target = ?)"
        params: list = [node_id, node_id] if d == "both" else [node_id]
        ek_clause, ek_p = _in_clause("kind", _norm(edge_kinds))
        sql = f"select id, source, target, kind, line, col from edges where {where}{ek_clause} limit 5000"
        edge_rows = c.execute(sql, params + ek_p).fetchall()

        edges: list[dict] = []
        neighbor_ids: list[str] = []
        seen: set[str] = set()
        for r in edge_rows:
            e = dict(r)
            edges.append(e)
            for end in (e.get("source"), e.get("target")):
                if end is not None and end != node_id and end not in seen:
                    seen.add(end)
                    neighbor_ids.append(end)

        nodes: list[dict] = []
        if neighbor_ids:
            ph = ",".join("?" for _ in neighbor_ids)
            node_rows = c.execute(
                f"select {_NODE_COLS} from nodes where id in ({ph})", neighbor_ids).fetchall()
            nodes = [_node_dict(r) for r in node_rows]
        return {"center": center, "nodes": nodes, "edges": edges}

    # ---------------------------- file-tree --------------------------

    def file_tree(self, prefix: str | None) -> list[dict]:
        c = self.conn
        p = (prefix or "").strip()
        if p:
            safe = p.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            rows = c.execute(
                "select path, language, size, node_count as \"nodeCount\" from files "
                "where path like ? escape '\\' order by path limit 5000", (safe + "%",)).fetchall()
        else:
            rows = c.execute(
                "select path, language, size, node_count as \"nodeCount\" from files "
                "order by path limit 5000").fetchall()
        return [dict(r) for r in rows]

    # ----------------------------- graph -----------------------------

    def graph(self, limit: int | None, languages: list[str] | None,
              kinds: list[str] | None, edge_kinds: list[str] | None) -> dict:
        c = self.conn
        lim = _clamp(limit, _DEFAULT_GRAPH_LIMIT, _MAX_GRAPH_LIMIT)
        node_sql = (
            f"select {_NODE_COLS} from nodes where 1=1"
        )
        params: list = []
        lang_clause, lang_p = _in_clause("language", _norm(languages))
        kind_clause, kind_p = _in_clause("kind", _norm(kinds))
        node_sql += lang_clause + kind_clause + (
            " order by case kind when 'file' then 0 when 'class' then 1 when 'interface' then 2 "
            "when 'method' then 3 when 'function' then 4 else 5 end, id limit ?")
        params += lang_p + kind_p + [lim]
        node_rows = c.execute(node_sql, params).fetchall()
        nodes = [_node_dict(r) for r in node_rows]
        node_ids = {n["id"] for n in nodes if n.get("id") is not None}

        ek_clause, ek_p = _in_clause("kind", _norm(edge_kinds))
        edge_rows = c.execute(
            f"select id, source, target, kind, line, col from edges where 1=1{ek_clause} limit ?",
            ek_p + [_MAX_EDGE_LIMIT]).fetchall()
        edges = []
        for r in edge_rows:
            e = dict(r)
            if e.get("source") in node_ids and e.get("target") in node_ids:
                edges.append(e)

        total_nodes = c.execute("select count(*) from nodes").fetchone()[0]
        total_edges = c.execute("select count(*) from edges").fetchone()[0]
        return {"nodes": nodes, "edges": edges, "totalNodes": total_nodes, "totalEdges": total_edges}
