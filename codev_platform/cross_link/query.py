"""Cross-layer KG 便捷查询 API。

典型用法：
    >>> from cross_link.query import CrossLayerDB
    >>> db = CrossLayerDB.default()
    >>> db.list_readers("stock_recommend_result")
    [{'method': 'ConfigurationQueryMapper.selectRecommendResults',
      'path': '.../ConfigurationQueryMapper.java', 'line': 42, ...}, ...]

返回值统一 dict，不暴露 sqlite cursor 细节。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .schema import DB_PATH


@dataclass
class CrossLayerDB:
    """Cross-layer KG 只读查询 wrapper。"""

    conn: sqlite3.Connection

    def __post_init__(self) -> None:
        # 保证直接 CrossLayerDB(conn) 构造也能用 row["col"] 索引
        self.conn.row_factory = sqlite3.Row

    @classmethod
    def default(cls) -> "CrossLayerDB":
        if not DB_PATH.exists():
            raise FileNotFoundError(
                f"cross_layer DB 不存在: {DB_PATH}; 先跑 python -m cross_link.build_index"
            )
        return cls(sqlite3.connect(DB_PATH))

    def close(self) -> None:
        self.conn.close()

    # ----------------------------------------------------------------------
    # 表反查（list_readers / list_writers / list_definers）
    # ----------------------------------------------------------------------

    def list_readers(self, table: str) -> list[dict]:
        """列出 SELECT/JOIN 该表的所有 java_method（rel='queries_table'）。"""
        return self._list_edge_sources(table, "queries_table")

    def list_writers(self, table: str) -> list[dict]:
        """列出 INSERT 该表的所有 java_method（rel='writes_table'）。"""
        return self._list_edge_sources(table, "writes_table")

    def list_updaters(self, table: str) -> list[dict]:
        """列出 UPDATE/DELETE 该表的所有 java_method（rel='updates_table'）。"""
        return self._list_edge_sources(table, "updates_table")

    def list_definers(self, table: str) -> list[dict]:
        """列出定义 / 变更该表的所有 Flyway migration（rel='defines_table'）。"""
        return self._list_edge_sources(table, "defines_table", src_kind="flyway_migration")

    def list_columns(self, table: str) -> list[dict]:
        """列出该表的所有列节点（按 defines_column 边推导）。"""
        cur = self.conn.execute(
            """SELECT c.name, c.id FROM nodes t
               JOIN nodes c ON c.parent_id = t.id AND c.kind = 'column'
               WHERE t.kind = 'table' AND t.name = ? AND t.path IS NULL
               ORDER BY c.id""",
            (table,),
        )
        return [{"column": r["name"], "id": r["id"]} for r in cur.fetchall()]

    def find_all_references(self, table: str) -> dict[str, list[dict]]:
        """一站汇总该表的所有跨层关系（Java + Python + Flyway）。"""
        return {
            "definers": self.list_definers(table),
            "java_readers":   self.list_readers(table),         # java_method queries
            "java_writers":   self.list_writers(table),
            "java_updaters":  self.list_updaters(table),
            "python_readers": self.list_python_readers(table),
            "python_writers": self.list_python_writers(table),
            "python_updaters": self.list_python_updaters(table),
        }

    # ----------------------------------------------------------------------
    # Python 仓储层反查（M2）
    # ----------------------------------------------------------------------

    def list_python_readers(self, table: str) -> list[dict]:
        """列出 SELECT 该表的所有 python_method。"""
        return self._list_edge_sources(table, "reads_table", src_kind="python_method")

    def list_python_writers(self, table: str) -> list[dict]:
        """列出 INSERT 该表的所有 python_method。"""
        return self._list_edge_sources(table, "writes_table", src_kind="python_method")

    def list_python_updaters(self, table: str) -> list[dict]:
        """列出 UPDATE/DELETE 该表的所有 python_method。"""
        return self._list_edge_sources(table, "updates_table", src_kind="python_method")

    # ----------------------------------------------------------------------
    # 前端 ↔ Java endpoint 双向查询（M1）
    # ----------------------------------------------------------------------

    def list_endpoint_callers(self, java_endpoint_name: str) -> list[dict]:
        """列出调用某 Java endpoint 的所有前端 API 函数。

        Args:
            java_endpoint_name: 如 'StockController.page'
        Returns:
            [{name, path, line, url, evidence}, ...]
        """
        import json as _json
        cur = self.conn.execute(
            """SELECT f.name, f.path, f.line, f.meta_json, e.evidence, e.confidence
               FROM nodes j
               JOIN edges e ON e.dst_id = j.id AND e.rel = 'calls_api'
               JOIN nodes f ON f.id = e.src_id AND f.kind = 'frontend_api'
               WHERE j.kind = 'java_endpoint' AND j.name = ?
               ORDER BY f.path, f.line""",
            (java_endpoint_name,),
        )
        out: list[dict] = []
        for r in cur.fetchall():
            meta = _json.loads(r["meta_json"]) if r["meta_json"] else {}
            out.append({
                "name": r["name"],
                "path": r["path"],
                "line": r["line"],
                "url": meta.get("url"),
                "evidence": r["evidence"],
                "confidence": r["confidence"],
            })
        return out

    def list_caller_endpoint(self, frontend_api_name: str) -> list[dict]:
        """列出某前端 API 函数调到的所有 Java endpoint（通常 1 条，多条意味着 URL 冲突）。"""
        import json as _json
        cur = self.conn.execute(
            """SELECT j.name, j.path, j.line, j.meta_json, e.evidence, e.confidence
               FROM nodes f
               JOIN edges e ON e.src_id = f.id AND e.rel = 'calls_api'
               JOIN nodes j ON j.id = e.dst_id AND j.kind = 'java_endpoint'
               WHERE f.kind = 'frontend_api' AND f.name = ?
               ORDER BY j.path, j.line""",
            (frontend_api_name,),
        )
        out: list[dict] = []
        for r in cur.fetchall():
            meta = _json.loads(r["meta_json"]) if r["meta_json"] else {}
            out.append({
                "name": r["name"],
                "path": r["path"],
                "line": r["line"],
                "url": meta.get("url"),
                "evidence": r["evidence"],
                "confidence": r["confidence"],
            })
        return out

    # ----------------------------------------------------------------------
    # 元数据 / 统计
    # ----------------------------------------------------------------------

    def stats(self) -> dict[str, int | str]:
        """返回当前 DB 统计 + build_meta。"""
        nodes_by_kind = {
            r["kind"]: r["c"]
            for r in self.conn.execute("SELECT kind, COUNT(*) c FROM nodes GROUP BY kind")
        }
        edges_by_rel = {
            r["rel"]: r["c"]
            for r in self.conn.execute("SELECT rel, COUNT(*) c FROM edges GROUP BY rel")
        }
        meta = {
            r["key"]: r["value"]
            for r in self.conn.execute("SELECT key, value FROM build_meta")
        }
        return {
            "nodes_by_kind": nodes_by_kind,
            "edges_by_rel": edges_by_rel,
            "build_meta": meta,
        }

    # ----------------------------------------------------------------------
    # 内部
    # ----------------------------------------------------------------------

    def _list_edge_sources(
        self, table: str, rel: str, src_kind: str = "java_method",
    ) -> list[dict]:
        cur = self.conn.execute(
            """SELECT n.name, n.path, n.line, n.kind, e.confidence, e.evidence, n.meta_json
               FROM nodes t
               JOIN edges e ON e.dst_id = t.id AND e.rel = ?
               JOIN nodes n ON n.id = e.src_id AND n.kind = ?
               WHERE t.kind = 'table' AND t.name = ? AND t.path IS NULL
               ORDER BY n.name, n.line""",
            (rel, src_kind, table),
        )
        out: list[dict] = []
        for r in cur.fetchall():
            meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
            out.append({
                "name": r["name"],
                "kind": r["kind"],
                "path": r["path"],
                "line": r["line"],
                "confidence": r["confidence"],
                "evidence": r["evidence"],
                "meta": meta,
            })
        return out
