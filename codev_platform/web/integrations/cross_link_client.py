"""cross-link 只读 SQLite 客户端 (plan §12.1 + §二十)。

吸收 Java codegraph-api 的 6 个 cross-link 接口 (stats/tables/table-refs/endpoint-link/
search-nodes/graph), 直读 per-project `cross_layer.sqlite`, **不经 daemon、不写库** → 零争用。

SQL + 装配逻辑逐句对齐 Java CrossLinkFacadeService, 字段形状一致。底层查询沿用本仓
cross_link.server 的 schema (nodes: id/kind/name/path/line/language/meta_json;
edges: src_id/rel/dst_id/confidence/evidence)。异常一律转 PlatformError。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from codev_platform.core.errors import ErrorCode, PlatformError
from codev_platform.core.paths import cross_link_db_path

_DEFAULT_SEARCH_LIMIT = 20
_MAX_SEARCH_LIMIT = 50
_DEFAULT_GRAPH_LIMIT = 3000
_MAX_GRAPH_LIMIT = 10000

_OVERVIEW_KINDS = {
    "frontend_page", "frontend_api", "java_endpoint",
    "table", "python_method", "flyway_migration",
}
_OVERVIEW_RELS = {
    "page_calls_api", "calls_api", "controller_calls_facade",
    "facade_calls_service", "service_calls_mapper",
    "queries_table", "writes_table", "reads_table",
    "updates_table", "defines_table",
}


def _db_path(project_id: str | None, db_path: str | Path | None) -> Path:
    if db_path is not None:
        return Path(db_path)
    if not project_id:
        raise PlatformError(ErrorCode.INVALID_PARAMS, "project_id is required for cross-link query")
    return cross_link_db_path(project_id)


def _connect_ro(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise PlatformError(
            ErrorCode.INDEX_MISSING,
            "cross-link index not found for this project.",
            detail=f"missing: {path}",
        )
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as exc:  # noqa: BLE001
        raise PlatformError(
            ErrorCode.UPSTREAM_UNAVAILABLE, "cross-link database unavailable.", detail=str(exc)
        ) from exc


def _parse_meta(raw) -> dict:
    if not raw:
        return {}
    try:
        m = json.loads(raw)
        return m if isinstance(m, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _clamp(value: int | None, default: int, maximum: int, minimum: int = 1) -> int:
    if value is None:
        return default
    return min(max(value, minimum), maximum)


def _norm_set(values: list[str] | None) -> set[str]:
    if not values:
        return set()
    return {v.strip() for v in values if v and v.strip()}


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# cross-link sqlite 历史把后端 HTTP 端点的 kind 存成 'java_endpoint' (业务仓 scanner
# 命名), 语义其实是"后端端点"通用槽位 (Python/Java/Node 端点同槽)。对外 API 契约统一用
# 语言中性的 'backend_endpoint'; DB 列值 / 内部 SQL / 节点 id 仍保持 java_endpoint
# (不动业务仓写侧)。仅在读边界做 kind 双向翻译。
_DB_ENDPOINT_KIND = "java_endpoint"
_API_ENDPOINT_KIND = "backend_endpoint"


def _kind_to_api(kind: str | None) -> str | None:
    """DB 原始 kind → 对外 kind (java_endpoint → backend_endpoint)。"""
    return _API_ENDPOINT_KIND if kind == _DB_ENDPOINT_KIND else kind


def _kind_to_db(kind: str) -> str:
    """对外 kind → DB 查询 kind (backend_endpoint → java_endpoint)。"""
    return _DB_ENDPOINT_KIND if kind == _API_ENDPOINT_KIND else kind


class CrossLinkClient:
    """per-request 只读客户端。用 with 管理连接。"""

    def __init__(self, project_id: str | None = None, *, db_path: str | Path | None = None) -> None:
        self._path = _db_path(project_id, db_path)
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> "CrossLinkClient":
        self._conn = _connect_ro(self._path)
        return self

    def __exit__(self, *exc) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise PlatformError(ErrorCode.INTERNAL, "cross-link client not opened")
        return self._conn

    # ----------------------------- stats -----------------------------

    def stats(self) -> dict:
        c = self.conn
        row = c.execute("select value from build_meta where key = 'last_build_at'").fetchone()
        last_build = row[0] if row is not None else None
        nodes_by_kind = {_kind_to_api(r["k"]): r["c"] for r in c.execute(
            "select kind as k, count(*) as c from nodes group by kind order by kind") if r["k"] is not None}
        edges_by_rel = {r["k"]: r["c"] for r in c.execute(
            "select rel as k, count(*) as c from edges group by rel order by rel") if r["k"] is not None}
        return {"lastBuildAt": last_build, "nodesByKind": nodes_by_kind, "edgesByRel": edges_by_rel}

    # ----------------------------- tables ----------------------------

    def tables(self) -> dict:
        rows = self.conn.execute(
            "select name from nodes where kind = 'table' and path is null order by name").fetchall()
        return {"tables": [r["name"] for r in rows]}

    # --------------------------- table-refs --------------------------

    def table_refs(self, table: str | None) -> dict:
        t = (table or "").strip()
        if not t:
            raise PlatformError(ErrorCode.INVALID_PARAMS, "table 不能为空")
        c = self.conn
        return {
            "table": t,
            "definers": self._edge_sources(c, t, "defines_table", "flyway_migration"),
            "javaReaders": self._edge_sources(c, t, "queries_table", "java_method"),
            "javaWriters": self._edge_sources(c, t, "writes_table", "java_method"),
            "javaUpdaters": self._edge_sources(c, t, "updates_table", "java_method"),
            "pythonReaders": self._edge_sources(c, t, "reads_table", "python_method"),
            "pythonWriters": self._edge_sources(c, t, "writes_table", "python_method"),
            "pythonUpdaters": self._edge_sources(c, t, "updates_table", "python_method"),
        }

    @staticmethod
    def _edge_sources(conn: sqlite3.Connection, table: str, rel: str, src_kind: str) -> list[dict]:
        rows = conn.execute(
            "select n.name, n.kind, n.path, n.line, e.confidence, e.evidence "
            "from nodes t "
            "join edges e on e.dst_id = t.id and e.rel = ? "
            "join nodes n on n.id = e.src_id and n.kind = ? "
            "where t.kind = 'table' and t.name = ? and t.path is null "
            "order by n.name, n.line",
            (rel, src_kind, table),
        ).fetchall()
        return [
            {"name": r["name"], "kind": r["kind"], "path": r["path"], "line": r["line"],
             "confidence": r["confidence"], "evidence": r["evidence"]}
            for r in rows
        ]

    # -------------------------- endpoint-link ------------------------

    def endpoint_link(self, name: str | None) -> list[dict]:
        qname = (name or "").strip()
        if not qname:
            raise PlatformError(ErrorCode.INVALID_PARAMS, "name 不能为空")
        c = self.conn
        src_rows = c.execute(
            "select id, kind, path, line, meta_json from nodes "
            "where name = ? and kind in ('frontend_api', 'java_endpoint')", (qname,)).fetchall()
        out: list[dict] = []
        for src in src_rows:
            meta = _parse_meta(src["meta_json"])
            kind = src["kind"]
            item = {
                "node": qname, "kind": _kind_to_api(kind), "path": src["path"], "line": src["line"],
                "url": meta.get("url"), "targets": [], "callers": [],
            }
            if kind == "frontend_api":
                item["direction"] = "frontend -> java"
                item["targets"] = self._endpoint_targets(
                    c, src["id"], src_side="src", dst_kind="java_endpoint")
            else:
                item["direction"] = "java <- frontend"
                item["callers"] = self._endpoint_targets(
                    c, src["id"], src_side="dst", dst_kind="frontend_api")
            out.append(item)
        return out

    @staticmethod
    def _endpoint_targets(conn: sqlite3.Connection, node_id, *, src_side: str, dst_kind: str) -> list[dict]:
        # src_side='src': 给定节点是 calls_api 的 src, 取 dst (下游 java);
        # src_side='dst': 给定节点是 calls_api 的 dst, 取 src (上游前端)。
        if src_side == "src":
            sql = ("select x.name, x.path, x.line, x.meta_json, e.confidence, e.evidence "
                   "from edges e join nodes x on x.id = e.dst_id and x.kind = ? "
                   "where e.src_id = ? and e.rel = 'calls_api' order by x.name, x.line")
        else:
            sql = ("select x.name, x.path, x.line, x.meta_json, e.confidence, e.evidence "
                   "from edges e join nodes x on x.id = e.src_id and x.kind = ? "
                   "where e.dst_id = ? and e.rel = 'calls_api' order by x.name, x.line")
        rows = conn.execute(sql, (dst_kind, node_id)).fetchall()
        out: list[dict] = []
        for r in rows:
            meta = _parse_meta(r["meta_json"])
            out.append({
                "name": r["name"], "path": r["path"], "line": r["line"],
                "url": meta.get("url"), "confidence": r["confidence"], "evidence": r["evidence"],
            })
        return out

    # -------------------------- search-nodes -------------------------

    def search_nodes(self, query: str | None, kind: str | None, limit: int | None) -> dict:
        q = (query or "").strip()
        if not q:
            raise PlatformError(ErrorCode.INVALID_PARAMS, "query 不能为空")
        k = (kind or "").strip() or "all"
        lim = _clamp(limit, _DEFAULT_SEARCH_LIMIT, _MAX_SEARCH_LIMIT)
        like = "%" + _escape_like(q) + "%"
        sql = ("select name, kind, path, line, language, meta_json from nodes "
               "where name like ? escape '\\' collate nocase ")
        params: list = [like]
        if k.lower() != "all":
            sql += "and kind = ? "
            params.append(_kind_to_db(k))
        sql += "order by kind, name limit ?"
        params.append(lim)
        rows = self.conn.execute(sql, params).fetchall()
        hits = [
            {"name": r["name"], "kind": _kind_to_api(r["kind"]), "path": r["path"], "line": r["line"],
             "language": r["language"], "meta": _parse_meta(r["meta_json"])}
            for r in rows
        ]
        return {"query": q, "kind": k, "hits": hits}

    # ----------------------------- graph -----------------------------

    def graph(self, mode: str | None, kinds: list[str] | None, exclude_kinds: list[str] | None,
              rels: list[str] | None, exclude_rels: list[str] | None, limit: int | None) -> dict:
        lim = _clamp(limit, _DEFAULT_GRAPH_LIMIT, _MAX_GRAPH_LIMIT)
        norm_mode = (mode or "").strip().lower()
        if norm_mode in ("", "overview"):
            norm_mode = "overview"
        elif norm_mode not in ("full", "kind"):
            norm_mode = "overview"

        if norm_mode == "full":
            return self._graph_full(lim)
        return self._graph_filtered(lim, norm_mode, kinds, exclude_kinds, rels, exclude_rels)

    def _graph_full(self, limit: int) -> dict:
        c = self.conn
        node_sql = (
            "select id, kind, name, path, line, language from nodes "
            "order by case kind when 'table' then 0 when 'java_endpoint' then 1 "
            "when 'frontend_api' then 2 when 'java_method' then 3 when 'python_method' then 4 "
            "when 'flyway_migration' then 5 when 'column' then 9 else 6 end, id limit ?")
        nodes = [self._graph_node(r) for r in c.execute(node_sql, (limit,)).fetchall()]
        edge_sql = (
            "select s.kind || ':' || e.src_id as src, t.kind || ':' || e.dst_id as dst, e.rel "
            "from edges e join nodes s on s.id = e.src_id join nodes t on t.id = e.dst_id")
        edges = [{"source": r["src"], "target": r["dst"], "kind": r["rel"]}
                 for r in c.execute(edge_sql).fetchall()]
        return {"nodes": nodes, "edges": edges, "nodeCount": len(nodes), "edgeCount": len(edges)}

    def _graph_filtered(self, limit: int, mode: str, kinds, exclude_kinds, rels, exclude_rels) -> dict:
        c = self.conn
        # 入参 kind 翻译回 DB 值 (前端传 backend_endpoint → 查 java_endpoint)。
        include_kinds = {_kind_to_db(k) for k in _norm_set(kinds)}
        exclude_k = {_kind_to_db(k) for k in _norm_set(exclude_kinds)}
        include_rels = _norm_set(rels)
        exclude_r = _norm_set(exclude_rels)
        if mode == "overview":
            include_kinds = include_kinds or set(_OVERVIEW_KINDS)
            include_rels = include_rels or set(_OVERVIEW_RELS)

        node_sql = "select id, kind, name, path, line, language from nodes where 1=1 "
        node_params: list = []
        if include_kinds:
            node_sql += "and kind in (" + ",".join("?" for _ in include_kinds) + ") "
            node_params += list(include_kinds)
        if exclude_k:
            node_sql += "and kind not in (" + ",".join("?" for _ in exclude_k) + ") "
            node_params += list(exclude_k)
        node_sql += (
            "order by case kind when 'frontend_page' then 0 when 'frontend_api' then 1 "
            "when 'java_endpoint' then 2 when 'table' then 3 when 'python_method' then 4 "
            "when 'flyway_migration' then 5 when 'java_method' then 6 when 'column' then 9 "
            "else 8 end, id limit ?")
        node_params.append(limit)
        node_rows = c.execute(node_sql, node_params).fetchall()
        nodes = [self._graph_node(r) for r in node_rows]
        selected = {n["id"] for n in nodes}

        edge_sql = (
            "select s.kind || ':' || e.src_id as src, t.kind || ':' || e.dst_id as dst, e.rel "
            "from edges e join nodes s on s.id = e.src_id join nodes t on t.id = e.dst_id where 1=1 ")
        edge_params: list = []
        if include_rels:
            edge_sql += "and e.rel in (" + ",".join("?" for _ in include_rels) + ") "
            edge_params += list(include_rels)
        if exclude_r:
            edge_sql += "and e.rel not in (" + ",".join("?" for _ in exclude_r) + ") "
            edge_params += list(exclude_r)
        edges = []
        for r in c.execute(edge_sql, edge_params).fetchall():
            if r["src"] in selected and r["dst"] in selected:
                edges.append({"source": r["src"], "target": r["dst"], "kind": r["rel"]})
        return {"nodes": nodes, "edges": edges, "nodeCount": len(nodes), "edgeCount": len(edges)}

    @staticmethod
    def _graph_node(r: sqlite3.Row) -> dict:
        # id 用 DB 原始 kind (与 edge 端点 's.kind||:||id' 一致, 不能翻译);
        # kind 字段对外翻译成语言中性值 (java_endpoint → backend_endpoint)。
        return {
            "id": f"{r['kind']}:{r['id']}", "kind": _kind_to_api(r["kind"]), "name": r["name"],
            "filePath": r["path"], "startLine": r["line"], "language": r["language"],
        }
