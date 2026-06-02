"""builtin.sql 插件测试 — detect 真值 (命中/不误命中) + analyze 合成 fixture 产出合法。

不绑项目名: detect 基于 repo 内容 (有 .sql / .sqlite / ORM 迹象)。analyze 在合成最小
SQL fixture 上产出非空 db_table / db_column 节点 + contains 边, 校验 schema 合法 +
方言写进 meta["dialect"] + node id 跨插件可链接形态。每测自清注册表。
"""
from __future__ import annotations

import pytest

from codev_platform.graph.schema import (
    AnalyzerResult,
    GraphEdge,
    GraphNode,
    NodeKind,
)

# table->column 边 kind: 裸字符串, 对齐 cross_link 适配器 defines_column 约定。
_DEFINES_COLUMN = "defines_column"
from codev_platform.plugins import clear_registry, registered_names, run_applicable
from codev_platform.plugins.builtin.sql import PLUGIN_NAME as SQL_NAME, SqlPlugin


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


# ---------------- detect: 基于 repo 内容, 不基于项目名 ----------------

def test_sql_detect_by_sql_file(tmp_path):
    (tmp_path / "schema.sql").write_text(
        "CREATE TABLE users (id INTEGER);", encoding="utf-8"
    )
    assert SqlPlugin().detect(tmp_path) is True


def test_sql_detect_by_sqlite_file(tmp_path):
    (tmp_path / "app.sqlite").write_bytes(b"SQLite format 3\x00")
    assert SqlPlugin().detect(tmp_path) is True


def test_sql_detect_by_orm_hint(tmp_path):
    (tmp_path / "models.py").write_text(
        "from sqlalchemy.orm import declarative_base\n"
        "Base = declarative_base()\n"
        "class User(Base):\n    __tablename__ = 'users'\n",
        encoding="utf-8",
    )
    assert SqlPlugin().detect(tmp_path) is True


def test_sql_detect_negative_no_db_traces(tmp_path):
    # 纯前端 repo, 无 .sql / .sqlite / ORM 迹象 -> 不误命中。
    (tmp_path / "App.tsx").write_text("export default () => null;", encoding="utf-8")
    (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")
    assert SqlPlugin().detect(tmp_path) is False


def test_sql_detect_skips_build_dirs(tmp_path):
    # .sql 只在被跳过的目录里 (node_modules) -> 不命中。
    nm = tmp_path / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / "bundled.sql").write_text("CREATE TABLE x (id INT);", encoding="utf-8")
    assert SqlPlugin().detect(tmp_path) is False


# ---------------- analyze: 合成 fixture, 验产出 + schema 合法 ----------------

def test_sql_analyze_extracts_tables_and_columns(tmp_path):
    (tmp_path / "schema.sql").write_text(
        "CREATE TABLE IF NOT EXISTS users (\n"
        "  id INTEGER PRIMARY KEY,\n"
        "  name VARCHAR(64) NOT NULL,\n"
        "  email TEXT,\n"
        "  PRIMARY KEY (id)\n"
        ");\n",
        encoding="utf-8",
    )
    result = SqlPlugin().analyze(tmp_path, "demo")
    assert isinstance(result, AnalyzerResult)

    tables = [n for n in result.nodes if n.kind == NodeKind.DB_TABLE.value]
    cols = [n for n in result.nodes if n.kind == NodeKind.DB_COLUMN.value]
    defines = [e for e in result.edges if e.kind == _DEFINES_COLUMN]

    assert len(tables) == 1
    assert tables[0].name == "users"
    assert tables[0].id == "demo:db_table:users"
    # PRIMARY KEY 表级约束行不算列; id/name/email 三列。
    col_names = {c.name for c in cols}
    assert col_names == {"id", "name", "email"}
    assert len(defines) == 3
    # defines_column 边: 裸字符串 kind + meta 留底原始 rel (对齐 cross_link 适配器)。
    for e in defines:
        assert e.kind == "defines_column"
        assert e.meta.get("cross_link_rel") == "defines_column"
    # 边端点都指向已产出的节点 id (schema 合法 + 可链接)。
    node_ids = {n.id for n in result.nodes}
    for e in defines:
        assert e.source in node_ids
        assert e.target in node_ids


def test_sql_analyze_schema_roundtrip_and_valid(tmp_path):
    (tmp_path / "t.sql").write_text(
        "CREATE TABLE orders (order_id BIGINT, amount DECIMAL);", encoding="utf-8"
    )
    result = SqlPlugin().analyze(tmp_path, "demo")
    # 全部产出都是合法 GraphNode / GraphEdge 且 to_dict/from_dict 可往返。
    for n in result.nodes:
        assert isinstance(n, GraphNode)
        assert n.project_id == "demo"
        assert n.language == "sql"
        assert GraphNode.from_dict(n.to_dict()).id == n.id
    for e in result.edges:
        assert isinstance(e, GraphEdge)
        assert GraphEdge.from_dict(e.to_dict()).kind == e.kind
    # AnalyzerResult 整体序列化往返。
    rebuilt = AnalyzerResult.from_dict(result.to_dict())
    assert len(rebuilt.nodes) == len(result.nodes)


def test_sql_analyze_dialect_in_meta(tmp_path):
    # mysql 特征 (AUTO_INCREMENT / ENGINE=) -> dialect=mysql。
    (tmp_path / "mysql_schema.sql").write_text(
        "CREATE TABLE t (id INT AUTO_INCREMENT) ENGINE=InnoDB;", encoding="utf-8"
    )
    # postgres 特征 (SERIAL)。
    (tmp_path / "pg_schema.sql").write_text(
        "CREATE TABLE u (id SERIAL, payload JSONB);", encoding="utf-8"
    )
    # 文件名后缀 *.sqlite.sql -> dialect=sqlite。
    (tmp_path / "00001.sqlite.sql").write_text(
        "CREATE TABLE v (id INTEGER);", encoding="utf-8"
    )
    result = SqlPlugin().analyze(tmp_path, "demo")
    by_table = {
        n.name: n.meta.get("dialect")
        for n in result.nodes
        if n.kind == NodeKind.DB_TABLE.value
    }
    assert by_table["t"] == "mysql"
    assert by_table["u"] == "postgres"
    assert by_table["v"] == "sqlite"


def test_sql_analyze_quoted_identifiers(tmp_path):
    # 引号包裹 + schema 前缀的表名/列名应被剥离归一。
    (tmp_path / "q.sql").write_text(
        'CREATE TABLE "app"."Accounts" (`Balance` NUMERIC, "Name" TEXT);',
        encoding="utf-8",
    )
    result = SqlPlugin().analyze(tmp_path, "demo")
    tables = [n for n in result.nodes if n.kind == NodeKind.DB_TABLE.value]
    assert len(tables) == 1
    assert tables[0].name == "Accounts"
    assert tables[0].id == "demo:db_table:accounts"  # 大小写归一 + schema 前缀剥离
    cols = {n.name for n in result.nodes if n.kind == NodeKind.DB_COLUMN.value}
    assert cols == {"Balance", "Name"}


# ---------------- 边契约 + 列抽取边形回归 (审计要求补) ----------------

def test_sql_table_column_edge_kind_is_defines_column(tmp_path):
    # taxonomy 契约: table->column 边必须是裸字符串 defines_column (不是 contains),
    # 且原始 rel 写进 meta["cross_link_rel"], 与 graph/adapters/cross_link.py 对齐。
    (tmp_path / "s.sql").write_text(
        "CREATE TABLE t (id INT, name TEXT);", encoding="utf-8"
    )
    result = SqlPlugin().analyze(tmp_path, "demo")
    assert result.edges, "应产出至少一条 defines_column 边"
    for e in result.edges:
        assert e.kind == "defines_column"
        assert e.meta.get("cross_link_rel") == "defines_column"
    # 不再产 contains 边。
    assert all(e.kind != "contains" for e in result.edges)


def test_sql_column_named_key_not_dropped(tmp_path):
    # Bug1 回归: 列名恰为 SQL 约束关键字 (key / index) 不应被当约束行丢弃,
    # 但真正的表级约束子句 (PRIMARY KEY / UNIQUE / KEY idx(...)) 仍要排除。
    (tmp_path / "k.sql").write_text(
        "CREATE TABLE t (\n"
        "  id INTEGER,\n"
        "  key VARCHAR(32),\n"
        "  index INTEGER,\n"
        "  PRIMARY KEY (id),\n"
        "  UNIQUE (key),\n"
        "  KEY idx_key (key)\n"
        ");\n",
        encoding="utf-8",
    )
    result = SqlPlugin().analyze(tmp_path, "demo")
    cols = {n.name for n in result.nodes if n.kind == NodeKind.DB_COLUMN.value}
    # 真实列: id / key / index 都在; 约束子句 (PRIMARY KEY/UNIQUE/KEY idx) 不算列。
    assert cols == {"id", "key", "index"}


def test_sql_quoted_keyword_column_kept(tmp_path):
    # 引号包裹的保留字列名 (`key` / "index") 必须保留为真实列, 不走约束判定。
    (tmp_path / "q.sql").write_text(
        'CREATE TABLE t (`key` INT, "index" TEXT, id INT);',
        encoding="utf-8",
    )
    result = SqlPlugin().analyze(tmp_path, "demo")
    cols = {n.name for n in result.nodes if n.kind == NodeKind.DB_COLUMN.value}
    assert cols == {"key", "index", "id"}


def test_sql_default_string_with_comma_not_split(tmp_path):
    # Bug2 回归: DEFAULT 字符串字面量内含逗号不能把一列切成两段垃圾列。
    (tmp_path / "d.sql").write_text(
        "CREATE TABLE t (\n"
        "  id INT,\n"
        "  note TEXT DEFAULT 'a,b,c',\n"
        "  name TEXT\n"
        ");\n",
        encoding="utf-8",
    )
    result = SqlPlugin().analyze(tmp_path, "demo")
    cols = {n.name for n in result.nodes if n.kind == NodeKind.DB_COLUMN.value}
    # 恰好 3 列, 无 'b' / 'c' 等被逗号误切出来的垃圾列。
    assert cols == {"id", "note", "name"}


def test_sql_decimal_precision_comma_not_split(tmp_path):
    # 组合列定义 numeric(10,2) / DECIMAL(10, 2) 括号内逗号不切, 列数正确。
    (tmp_path / "n.sql").write_text(
        "CREATE TABLE t (id INT, amount NUMERIC(10,2), rate DECIMAL(8, 4));",
        encoding="utf-8",
    )
    result = SqlPlugin().analyze(tmp_path, "demo")
    cols = {n.name for n in result.nodes if n.kind == NodeKind.DB_COLUMN.value}
    assert cols == {"id", "amount", "rate"}


def test_sql_inline_foreign_key_constraint_excluded(tmp_path):
    # 内联 FK 列 (uid ... REFERENCES) 仍是真实列; 独立 FOREIGN KEY / CONSTRAINT 行排除。
    (tmp_path / "fk.sql").write_text(
        "CREATE TABLE orders (\n"
        "  id INT,\n"
        "  uid INTEGER REFERENCES users(id),\n"
        "  CONSTRAINT fk_uid FOREIGN KEY (uid) REFERENCES users(id)\n"
        ");\n",
        encoding="utf-8",
    )
    result = SqlPlugin().analyze(tmp_path, "demo")
    cols = {n.name for n in result.nodes if n.kind == NodeKind.DB_COLUMN.value}
    # uid 是带内联 FK 的真实列; CONSTRAINT/FOREIGN KEY 行不是列。
    assert cols == {"id", "uid"}


# ---------------- 自动发现: 新插件免改 registry 即注册 ----------------

def test_sql_plugin_autodiscovered():
    names = registered_names()
    assert SQL_NAME in names


def test_run_applicable_includes_sql_on_sql_repo(tmp_path):
    (tmp_path / "schema.sql").write_text(
        "CREATE TABLE users (id INTEGER, name TEXT);", encoding="utf-8"
    )
    results = run_applicable(tmp_path, "demo")
    by_name = {r.plugin: r for r in results}
    assert SQL_NAME in by_name
    assert by_name[SQL_NAME].ok
    assert by_name[SQL_NAME].summary["nodes"] > 0
