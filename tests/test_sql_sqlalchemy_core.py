"""builtin.sql P1: SQLAlchemy Core imperative Table() 检测 + 测试夹具排除。

- Core `Table("orgs", metadata, Column("org_id", Text), ...)` → 权威 db_table/db_column
  (source=sqlalchemy-core), declarative 扫描漏它 (本仓 web/db/tables.py 的真实写法)。
- tests/ 路径下的 CREATE TABLE / Table() = 测试夹具, 不当生产表 (治幽灵表噪音)。
"""
from __future__ import annotations

from codev_platform.graph.schema import NodeKind
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
