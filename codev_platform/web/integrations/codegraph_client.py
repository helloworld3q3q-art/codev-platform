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
# 图谱可读密度上限: 返回边数 ∝ 节点数。防超大稠密项目(如 ideas-v2 的 Java 核心)在 overview
# 里返回"发丝团": 实测 limit=2000 时 ideas-v2 11106 边(5.6 边/节点)vs openclaw 5814(2.9)/
# codev 4526(2.3)。取 3 边/节点 → 只把过密项目压到与其它项目同量级, 本就稀疏的不受影响。
_EDGE_PER_NODE_CAP = 3

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

    def __enter__(self) -> CodegraphClient:
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
               kinds: list[str] | None, limit: int | None,
               *, match_mode: str = "and") -> list[dict]:
        """FTS 前缀搜。match_mode='and'(默认, 所有词都中, 精确)/ 'or'(任一词中, 宽松召回,
        bm25 仍把多词命中排前)。融合召回的 lane 走 'or' —— verbose 多词 query(混入描述词
        如 'function definition')AND 会全灭, OR 才能让目标符号被 bm25 顶上来。"""
        kw = (keyword or "").strip()
        if not kw:
            raise PlatformError(ErrorCode.INVALID_PARAMS, "keyword 不能为空")
        lim = _clamp(limit, _DEFAULT_SEARCH_LIMIT, _MAX_SEARCH_LIMIT)
        fts_query = self._build_fts_prefix(kw, match_mode=match_mode)
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
    def _build_fts_prefix(kw: str, *, match_mode: str = "and") -> str:
        parts = []
        for t in kw.split():
            if not t:
                continue
            escaped = t.replace('"', '""')
            parts.append(f'"{escaped}"*')
        if not parts:
            return '""'
        joiner = " OR " if match_mode == "or" else " "   # FTS5: 空格=隐式 AND
        return joiner.join(parts)

    # ----------------------------- node ------------------------------

    def node(self, node_id: str | None) -> dict | None:
        if not node_id:
            raise PlatformError(ErrorCode.INVALID_PARAMS, "id 不能为空")
        r = self.conn.execute(f"select {_NODE_COLS} from nodes where id = ?", (node_id,)).fetchone()
        return _node_dict(r) if r is not None else None

    # --------------------------- iter-nodes --------------------------

    def iter_nodes(self, *, kinds: list[str] | None = None):
        """流式枚举全部节点(向量索引建库用)。只读, 不一次性载内存。

        kinds 可限定节点种类(如 function/method/class); 缺省全取(语言差异大, 不硬筛 kind,
        由建库侧按文本质量自然取舍)。逐行 yield 与单点 node()/search() 同形(camelCase 别名)。
        """
        kind_clause, kind_p = _in_clause("kind", _norm(kinds))
        sql = f"select {_NODE_COLS} from nodes where 1=1{kind_clause}"
        for r in self.conn.execute(sql, kind_p):
            yield _node_dict(r)

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
              kinds: list[str] | None, edge_kinds: list[str] | None,
              *, edge_limit: int | None = None) -> dict:
        """返回**连通**子图: 边优先选取, 节点由所选边的端点派生 → 每条边两端必在节点集。

        旧做法是节点优先(按 degree 取 top-N 节点, 再只留两端都在 N 内的边): N 小时
        (默认 limit)绝大多数边被滤光 → 前端图全是散点无连线(ideas-v2 limit=50 实测
        edges=0; openclaw 可见边 669/35546)。

        新做法 "先选边再带节点":
        1. 按边重要性排 top 边 —— `calls` 边优先(调用关系是图谱主信息), 再按两端 degree
           之和降序(挑连接最密的核心区域)。
        2. 把这些边的端点收成节点集, 受 `limit`(节点上限)裁剪; 被裁掉节点关联的边一并丢弃,
           保证返回的 edges 两端都在 nodes 内(真连通)。
        languages/kinds 过滤节点候选, edge_kinds 过滤边; `edge_limit` 可选限边总量。
        """
        c = self.conn
        node_cap = _clamp(limit, _DEFAULT_GRAPH_LIMIT, _MAX_GRAPH_LIMIT)
        edge_cap = _clamp(edge_limit, _MAX_EDGE_LIMIT, _MAX_EDGE_LIMIT)
        # 按节点数比例收口边总量, 保证可读密度(避免超大稠密项目返回发丝团; 见 _EDGE_PER_NODE_CAP)。
        edge_cap = min(edge_cap, node_cap * _EDGE_PER_NODE_CAP)

        # 节点候选: 受 languages/kinds 过滤的 id 集合(供边筛选, kinds=None 时不限)。
        lang_clause, lang_p = _in_clause("language", _norm(languages))
        kind_clause, kind_p = _in_clause("kind", _norm(kinds))
        node_filter_active = bool(lang_clause or kind_clause)
        allowed_ids: set[str] | None = None
        if node_filter_active:
            id_rows = c.execute(
                f"select id from nodes where 1=1{lang_clause}{kind_clause}",
                lang_p + kind_p).fetchall()
            allowed_ids = {r[0] for r in id_rows}
            if not allowed_ids:
                total_nodes = c.execute("select count(*) from nodes").fetchone()[0]
                total_edges = c.execute("select count(*) from edges").fetchone()[0]
                return {"nodes": [], "edges": [], "totalNodes": total_nodes, "totalEdges": total_edges}

        # 边优先: calls 边优先, 再按两端 degree 之和降序 → 取图谱最密核心。
        ek_clause, ek_p = _in_clause("e.kind", _norm(edge_kinds))
        edge_sql = (
            "with deg as (select nid, count(*) c from "
            "(select source nid from edges union all select target from edges) group by nid) "
            "select e.id, e.source, e.target, e.kind, e.line, e.col "
            "from edges e "
            "left join deg ds on ds.nid = e.source "
            "left join deg dt on dt.nid = e.target "
            f"where 1=1{ek_clause} "
            "order by (case when e.kind = 'calls' then 1 else 0 end) desc, "
            "(coalesce(ds.c,0) + coalesce(dt.c,0)) desc, e.id limit ?"
        )
        # 多取一些候选边(node_cap 个节点最多承载远多于 node_cap 条边), 再受 edge_cap 收口。
        edge_rows = c.execute(edge_sql, ek_p + [edge_cap]).fetchall()

        # 逐边纳入: 端点满足节点过滤才计, 节点集到 node_cap 后只接纳"两端均已在集内"的边。
        node_ids: set[str] = set()
        edges: list[dict] = []
        for r in edge_rows:
            e = dict(r)
            s, t = e.get("source"), e.get("target")
            if s is None or t is None:
                continue
            if allowed_ids is not None and (s not in allowed_ids or t not in allowed_ids):
                continue
            new_endpoints = {x for x in (s, t) if x not in node_ids}
            if len(node_ids) + len(new_endpoints) > node_cap:
                # 节点已满: 仅当这条边两端都已在集内才保留(不再扩节点)。
                if s in node_ids and t in node_ids:
                    edges.append(e)
                continue
            node_ids.update(new_endpoints)
            edges.append(e)
            if len(edges) >= edge_cap:
                break

        nodes: list[dict] = []
        if node_ids:
            ids = list(node_ids)
            ph = ",".join("?" for _ in ids)
            node_rows = c.execute(
                f"select {_NODE_COLS} from nodes where id in ({ph})", ids).fetchall()
            nodes = [_node_dict(r) for r in node_rows]

        total_nodes = c.execute("select count(*) from nodes").fetchone()[0]
        total_edges = c.execute("select count(*) from edges").fetchone()[0]
        return {"nodes": nodes, "edges": edges, "totalNodes": total_nodes, "totalEdges": total_edges}
