"""builtin.sql P1/P2: SQLAlchemy Core imperative Table() 检测 + Core DML 读写血缘。

P1 (表定义):
- Core `Table("orgs", metadata, Column("org_id", Text), ...)` → 权威 db_table/db_column
  (source=sqlalchemy-core), declarative 扫描漏它 (本仓 web/db/tables.py 的真实写法)。
- tests/ 路径下的 CREATE TABLE / Table() = 测试夹具, 不当生产表 (治幽灵表噪音)。

P2 (DML 读写血缘):
- select()/insert()/update()/delete() builder 调用点 → reads/writes_table 边。
- 两阶段别名: 全局 `<var> = Table("x")` + 函数内 `o = tables.x`。
- 跨函数传表: `_upsert_stmt(tables.X, ...)` 归调用方写该表。
"""
from __future__ import annotations

from codev_platform.graph.schema import EdgeKind, NodeKind
from codev_platform.plugins.builtin.sql import SqlPlugin

_CORE_TABLES = '''\
from sqlalchemy import Table, Column, Text, MetaData
metadata = MetaData()
orgs = Table("orgs", metadata, Column("org_id", Text, primary_key=True), Column("name", Text))
users = Table("users", metadata, Column("username", Text), Column("org_id", Text))
'''


def _tables(result):
    return {n.name.lower(): n for n in result.nodes if n.kind == NodeKind.DB_TABLE.value}


def _cols(result, table):
    return {
        n.name.lower() for n in result.nodes
        if n.kind == NodeKind.DB_COLUMN.value and (n.meta or {}).get("table", "").lower() == table
    }


def test_core_table_produces_authoritative_db_table(tmp_path):
    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "tables.py").write_text(_CORE_TABLES, encoding="utf-8")
    result = SqlPlugin().analyze(tmp_path, "demo")
    tabs = _tables(result)
    assert "orgs" in tabs and "users" in tabs
    # source 标 sqlalchemy-core (权威, 区别于 dml-inferred 桩)
    assert tabs["orgs"].meta.get("source") == "sqlalchemy-core"
    # 列名取自 Column 的第一个串实参 (Core 显式列名)
    assert _cols(result, "orgs") == {"org_id", "name"}
    assert _cols(result, "users") == {"username", "org_id"}


def test_tests_path_table_is_excluded(tmp_path):
    # 生产源里有真表
    (tmp_path / "schema.sql").write_text("CREATE TABLE real_t (id INT);", encoding="utf-8")
    # tests/ 夹具里的 CREATE TABLE 是噪音, 不应进图谱
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_fixture.py").write_text(
        'SQL = "CREATE TABLE phantom_t (id INT)"', encoding="utf-8"
    )
    # Core Table() 在 tests/ 下也排除
    (tmp_path / "tests" / "conftest.py").write_text(
        'from sqlalchemy import Table, Column, Text, MetaData\n'
        'm = MetaData()\n'
        'ghost = Table("ghost_t", m, Column("x", Text))\n',
        encoding="utf-8",
    )
    result = SqlPlugin().analyze(tmp_path, "demo")
    tabs = _tables(result)
    assert "real_t" in tabs
    assert "phantom_t" not in tabs
    assert "ghost_t" not in tabs


# ============================ P2: Core DML 读写血缘 ============================

PID = "demo"

# 表定义 (权威 db_table)。Core DML 别名解析的全局阶段从这里取 {orgs->orgs, users->users, ...}。
_TABLES_PY = '''\
from sqlalchemy import Table, Column, Text, MetaData
metadata = MetaData()
orgs = Table("orgs", metadata, Column("org_id", Text), Column("name", Text))
users = Table("users", metadata, Column("user_id", Text))
org_members = Table("org_members", metadata, Column("org_id", Text), Column("user_id", Text))
audit = Table("audit", metadata, Column("id", Text))
'''

# 仓库层 (镜像 web/repositories/account_store_pg.py 真实 Core 写法)。
_STORE_PY = '''\
from sqlalchemy import delete, insert, select, update
from db import tables


def _upsert_stmt(table, values):
    from sqlalchemy.dialects.postgresql import insert as _insert
    stmt = _insert(table).values(**values)
    return stmt.on_conflict_do_update(index_elements=["id"], set_={})


def get_org(conn, code):
    o = tables.orgs
    return conn.execute(select(o.c.org_id, o.c.name).where(o.c.org_id == code)).first()


def list_members(conn, org_id):
    u = tables.users
    om = tables.org_members
    return conn.execute(
        select(u.c.user_id)
        .select_from(u.join(om, om.c.user_id == u.c.user_id))
        .where(om.c.org_id == org_id)
    ).all()


def add_audit(conn, row):
    a = tables.audit
    conn.execute(insert(a).values(**row))


def touch_org(conn, code):
    o = tables.orgs
    conn.execute(update(o).where(o.c.org_id == code).values(name="x"))


def remove_member(conn, org_id, user):
    om = tables.org_members
    conn.execute(delete(om).where(om.c.org_id == org_id, om.c.user_id == user))


def upsert_org(conn, code):
    stmt = _upsert_stmt(tables.orgs, {"org_id": code})
    conn.execute(stmt)
'''


def _analyze_dml(tmp_path):
    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "tables.py").write_text(_TABLES_PY, encoding="utf-8")
    (tmp_path / "store.py").write_text(_STORE_PY, encoding="utf-8")
    return SqlPlugin().analyze(tmp_path, PID)


def _edges(result, kind):
    return [(e.source, e.target) for e in result.edges if e.kind == kind]


def _fid(func):
    return f"{PID}:backend_function:store.py:{func}"


def test_core_select_produces_reads(tmp_path):
    r = _analyze_dml(tmp_path)
    reads = _edges(r, EdgeKind.READS_TABLE.value)
    # o = tables.orgs 别名解析 + select(o.c.*) -> 读 orgs。
    assert (_fid("get_org"), f"{PID}:db_table:orgs") in reads


def test_core_join_select_produces_two_reads(tmp_path):
    r = _analyze_dml(tmp_path)
    reads = _edges(r, EdgeKind.READS_TABLE.value)
    src = _fid("list_members")
    # select_from(u.join(om, ...)) -> 读 users + org_members。
    assert (src, f"{PID}:db_table:users") in reads
    assert (src, f"{PID}:db_table:org_members") in reads


def test_core_insert_produces_writes(tmp_path):
    r = _analyze_dml(tmp_path)
    writes = _edges(r, EdgeKind.WRITES_TABLE.value)
    assert (_fid("add_audit"), f"{PID}:db_table:audit") in writes


def test_core_update_produces_writes(tmp_path):
    r = _analyze_dml(tmp_path)
    writes = _edges(r, EdgeKind.WRITES_TABLE.value)
    assert (_fid("touch_org"), f"{PID}:db_table:orgs") in writes


def test_core_delete_produces_writes(tmp_path):
    r = _analyze_dml(tmp_path)
    writes = _edges(r, EdgeKind.WRITES_TABLE.value)
    assert (_fid("remove_member"), f"{PID}:db_table:org_members") in writes


def test_core_upsert_call_attributes_write_to_caller(tmp_path):
    r = _analyze_dml(tmp_path)
    writes = _edges(r, EdgeKind.WRITES_TABLE.value)
    # _upsert_stmt(tables.orgs, ...) 调用点把表归给调用方 upsert_org, 不归 helper。
    assert (_fid("upsert_org"), f"{PID}:db_table:orgs") in writes
    # helper _upsert_stmt 内部形参 table 解析不到 -> 不产边 (不归 helper)。
    helper_writes = [s for s, _ in writes if s == _fid("_upsert_stmt")]
    assert helper_writes == []


def test_core_dml_edges_point_to_existing_nodes(tmp_path):
    r = _analyze_dml(tmp_path)
    ids = {n.id for n in r.nodes}
    for e in r.edges:
        assert e.source in ids
        assert e.target in ids


def test_core_dml_confidence_below_one(tmp_path):
    r = _analyze_dml(tmp_path)
    # Core builder 推断 confidence=0.9 (区别于 raw SQL 字面量的 1.0)。
    edge = next(
        e for e in r.edges
        if e.source == _fid("get_org") and e.kind == EdgeKind.READS_TABLE.value
    )
    assert edge.confidence == 0.9


def test_core_dml_no_phantom_table_for_unresolved(tmp_path):
    r = _analyze_dml(tmp_path)
    # 全部表都来自 tables.py 权威定义, 不应有 dml-inferred 桩 (别名全解析成功)。
    tabs = _tables(r)
    for name in ("orgs", "users", "org_members", "audit"):
        assert tabs[name].meta.get("source") == "sqlalchemy-core"
