"""builtin.sql 的 Python DML 读写血缘测试 (unified-graph-lineage P2)。

验证 .py 里 raw SQL 字符串 -> backend_function 节点 + reads/writes_table 边:
- INSERT/UPDATE/DELETE -> writes_table; SELECT FROM/JOIN -> reads_table
- 边归属到最内层函数 (模块级 SQL 归属到 <file>)
- 已 DDL 定义的表直接连边; 未定义表建 inferred db_table stub (血缘不断)
- DDL 必须先于 DML: 真表不被 stub 覆盖 (保留列)
"""
from __future__ import annotations

from pathlib import Path

from codev_platform.graph.schema import EdgeKind, NodeKind
from codev_platform.plugins.builtin.sql import SqlPlugin

PID = "demo"

_SCHEMA = """\
CREATE TABLE orders (order_id BIGINT, amount DECIMAL);
CREATE TABLE users (id INTEGER, name TEXT);
"""

_REPO = '''\
def save_order(conn, row):
    conn.execute("INSERT INTO orders (order_id, amount) VALUES (%s, %s)", row)

def list_orders(conn):
    return conn.execute("SELECT order_id, amount FROM orders WHERE amount > %s")

def archive(conn):
    conn.execute("INSERT INTO audit_log (msg) VALUES (%s)", ("x",))

def join_read(conn):
    return conn.execute("SELECT u.name FROM users u JOIN orders o ON o.uid = u.id")
'''


def _analyze(tmp_path: Path):
    (tmp_path / "schema.sql").write_text(_SCHEMA, encoding="utf-8")
    (tmp_path / "repo.py").write_text(_REPO, encoding="utf-8")
    return SqlPlugin().analyze(tmp_path, PID)


def _edges(result, kind):
    return [(e.source, e.target) for e in result.edges if e.kind == kind]


def test_writes_table_from_insert(tmp_path: Path) -> None:
    r = _analyze(tmp_path)
    writes = _edges(r, EdgeKind.WRITES_TABLE.value)
    assert (f"{PID}:backend_function:repo.py:save_order", f"{PID}:db_table:orders") in writes


def test_reads_table_from_select(tmp_path: Path) -> None:
    r = _analyze(tmp_path)
    reads = _edges(r, EdgeKind.READS_TABLE.value)
    assert (f"{PID}:backend_function:repo.py:list_orders", f"{PID}:db_table:orders") in reads


def test_join_produces_two_reads(tmp_path: Path) -> None:
    r = _analyze(tmp_path)
    reads = _edges(r, EdgeKind.READS_TABLE.value)
    src = f"{PID}:backend_function:repo.py:join_read"
    assert (src, f"{PID}:db_table:users") in reads
    assert (src, f"{PID}:db_table:orders") in reads


def test_backend_function_node_created(tmp_path: Path) -> None:
    r = _analyze(tmp_path)
    funcs = {n.name: n for n in r.nodes if n.kind == NodeKind.BACKEND_FUNCTION.value}
    assert "save_order" in funcs
    assert funcs["save_order"].language == "python"
    assert funcs["save_order"].file == "repo.py"


def test_unknown_table_gets_inferred_stub(tmp_path: Path) -> None:
    r = _analyze(tmp_path)
    tables = {n.name: n for n in r.nodes if n.kind == NodeKind.DB_TABLE.value}
    assert "audit_log" in tables  # 无 DDL, 但 DML 引用 -> stub
    assert tables["audit_log"].meta.get("inferred") is True


def test_real_table_not_overwritten_by_stub(tmp_path: Path) -> None:
    r = _analyze(tmp_path)
    orders = next(
        n for n in r.nodes
        if n.kind == NodeKind.DB_TABLE.value and n.name == "orders"
    )
    # DDL 真表保留: 非 inferred, 且其列仍在 (DDL 先于 DML, stub 被 _absorb 丢弃)。
    assert orders.meta.get("inferred") is not True
    cols = {n.name for n in r.nodes if n.kind == NodeKind.DB_COLUMN.value}
    assert {"order_id", "amount"} <= cols


def test_edges_point_to_existing_nodes(tmp_path: Path) -> None:
    r = _analyze(tmp_path)
    ids = {n.id for n in r.nodes}
    for e in r.edges:
        assert e.source in ids
        assert e.target in ids


def test_module_level_sql_owner_is_file(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        'q = "SELECT id FROM users"\n', encoding="utf-8"
    )
    r = SqlPlugin().analyze(tmp_path, PID)
    funcs = [n for n in r.nodes if n.kind == NodeKind.BACKEND_FUNCTION.value]
    assert any(n.name == "<m.py>" for n in funcs)
