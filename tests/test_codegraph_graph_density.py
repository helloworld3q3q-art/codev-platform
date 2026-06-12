"""web /codegraph/graph overview 密度上限: 边数 ∝ 节点数, 防超大稠密项目返回"发丝团"。

背景: edge-first 选边修了"散点无连线", 但对 ideas-v2 这种 28万节点稠密 Java 核心又过度 —
limit=2000 返回 11106 边(5.6 边/节点)成发丝团。_EDGE_PER_NODE_CAP=3 把边数收口到 node_cap*3,
只压过密项目, 不影响本就稀疏的(openclaw 2.9 / codev 2.3 边/节点 < 3 不变)。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from codev_platform.web.integrations.codegraph_client import (
    CodegraphClient,
    _EDGE_PER_NODE_CAP,
)

# 与真 codegraph schema 对齐(节点列名 file_path/start_column/docstring 等, 见 test_web_graph)。
_SCHEMA = """
CREATE TABLE nodes (id TEXT, kind TEXT, name TEXT, qualified_name TEXT, file_path TEXT,
    language TEXT, start_line INTEGER, end_line INTEGER, start_column INTEGER,
    end_column INTEGER, docstring TEXT, signature TEXT, visibility TEXT,
    is_exported INTEGER, is_async INTEGER, is_static INTEGER, is_abstract INTEGER,
    decorators TEXT, type_parameters TEXT, updated_at INTEGER);
CREATE TABLE edges (id INTEGER PRIMARY KEY, source TEXT, target TEXT, kind TEXT,
    metadata TEXT, line INTEGER, col INTEGER, provenance TEXT);
"""


def _seed_dense(path: Path, n_nodes: int, n_edges_per_pair: int) -> None:
    """造一个稠密近完全图: n_nodes 个 calls 互连节点 → 远超 node_cap*3 的潜在边。"""
    c = sqlite3.connect(str(path))
    c.executescript(_SCHEMA)
    for i in range(n_nodes):
        c.execute("INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, start_line) "
                  "VALUES (?,?,?,?,?,?,?)",
                  (f"n{i}", "method", f"m{i}", f"C.m{i}", "A.java", "java", i))
    # 近完全图的 calls 边(稠密核心)
    eid = 0
    for i in range(n_nodes):
        for j in range(n_nodes):
            if i != j:
                for _ in range(n_edges_per_pair):
                    c.execute("INSERT INTO edges (id, source, target, kind) VALUES (?,?,?,?)",
                              (eid, f"n{i}", f"n{j}", "calls"))
                    eid += 1
    c.commit()
    c.close()


def test_graph_edge_density_capped(tmp_path):
    """稠密图: 返回边数 ≤ node_cap * _EDGE_PER_NODE_CAP, 不返回发丝团。"""
    db = tmp_path / "cg.db"
    _seed_dense(db, n_nodes=60, n_edges_per_pair=1)  # 60*59=3540 条潜在 calls 边
    with CodegraphClient(db_path=db) as cli:
        g = cli.graph(20, None, None, None)  # node_cap=20
    n, e = len(g["nodes"]), len(g["edges"])
    assert n <= 20, f"节点应 ≤ node_cap=20, 实得 {n}"
    assert e <= 20 * _EDGE_PER_NODE_CAP, f"边应 ≤ 20*{_EDGE_PER_NODE_CAP}={20*_EDGE_PER_NODE_CAP}, 实得 {e}"
    # 仍连通(每边两端都在节点集内)
    ids = {node["id"] for node in g["nodes"]}
    for edge in g["edges"]:
        assert edge["source"] in ids and edge["target"] in ids, "返回边的端点必须都在节点集内"


def test_graph_sparse_unaffected(tmp_path):
    """稀疏图(边/节点 < cap): 不被 cap 误伤, 全边返回。"""
    db = tmp_path / "cg2.db"
    # 10 节点链式 9 边 → 0.9 边/节点, 远低于 cap
    c = sqlite3.connect(str(db))
    c.executescript(_SCHEMA)
    for i in range(10):
        c.execute("INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, start_line) "
                  "VALUES (?,?,?,?,?,?,?)",
                  (f"n{i}", "method", f"m{i}", f"C.m{i}", "A.java", "java", i))
    for i in range(9):
        c.execute("INSERT INTO edges (id, source, target, kind) VALUES (?,?,?,?)",
                  (i, f"n{i}", f"n{i+1}", "calls"))
    c.commit()
    c.close()
    with CodegraphClient(db_path=db) as cli:
        g = cli.graph(2000, None, None, None)
    assert len(g["edges"]) == 9, "稀疏图应全边返回, 不被密度 cap 削减"
