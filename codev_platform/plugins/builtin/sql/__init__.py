"""builtin.sql — 通用 SQL / 数据库栈插件 (Layer 3 DB, 不绑任何具体项目)。

taxonomy 定位 (对齐 agent-provider-architecture: 按基座/适配/方言分, 不按项目/厂商):
  - Layer 3 = DB 家族。SQL 是一个 scanner, 方言 sqlite / mysql / postgres 是同一
    scanner 的 meta["dialect"] 维度 (不是三个插件)。这是 endpoint -> table 链路的
    table 层 (前端 calls_api -> backend defines_api -> backend reads/writes db_table)。

detect (基于 repo 内容, 不基于项目名 / 目录名):
  - repo 内存在 *.sql 文件, 或
  - repo 内存在 *.sqlite / *.db / *.sqlite3 文件 (排除构建物 / .venv / data 目录), 或
  - ORM 迹象 (SQLAlchemy declarative / Django models / Flyway migration 目录特征字面量)。

analyze:
  - 扫 SQL 文件里的 CREATE TABLE -> db_table 节点 + 列定义 -> db_column 节点。
  - db_table --defines_column--> db_column 边 (裸字符串 kind; 前端 unifiedgraph /
    codegraph utils 真消费此边渲染"定义字段"关系, 跨 DB 源 (.sql / python-ddl / ORM)
    同 kind 可链接)。
  - 方言推断写进 db_table 节点 meta["dialect"] (sqlite / mysql / postgres / unknown):
    文件名后缀 (*.sqlite.sql) / 内容特征 (AUTO_INCREMENT=mysql, SERIAL=pg, AUTOINCREMENT=sqlite)。

第一版轻量正则解析 (plan 允许"不追求覆盖所有写法"): 只解析最常见的
`CREATE TABLE [IF NOT EXISTS] name (col type ..., ...)` 形态。约束 / 索引 / 复杂表达式
列暂不深解析, 但保证产出非空且 schema 合法, 跨插件 node id 可链接。

node id 统一 "<project_id>:<kind>:<stable-key>" (与 _stack_scan / 其它栈插件同构):
  - db_table:  "<pid>:db_table:<table_name_lower>"
  - db_column: "<pid>:db_column:<table_name_lower>.<col_name_lower>"
表名做大小写归一 (lower), 保证 endpoint->table 链路里 reads/writes_table 边能命中。

> 2026-06 结构拆分 (sql.py 1254 行 -> sql/ 包): 三大扫描域分文件 (ddl / orm / core),
> 共享 helper 在 _common; 本 __init__ 保留 SqlPlugin (其 __module__ 须等于
> "codev_platform.plugins.builtin.sql" 以被 registry._discover_builtins 发现) + 全部
> 子模块符号 re-export, 对外 import 路径不变。纯结构移动, 零逻辑改动。
"""
from __future__ import annotations

import logging
from pathlib import Path

from codev_platform.graph.schema import (
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
    ProvSource,
)
from codev_platform.plugins.base import AnalyzerPlugin
from codev_platform.plugins.builtin import _stack_scan
from codev_platform.plugins.builtin.sql._common import (
    _REL_DEFINES_COLUMN,
    _call_attr_chain,
    _emit_table_access,
    _emit_table_nodes,
    _is_test_path,
    _NON_TABLE,
    _plausible_table,
    _RE_TEST_PATH,
    _str_const,
    _unquote,
)
from codev_platform.plugins.builtin.sql.ddl import (
    _CONSTRAINT_KW_AMBIG,
    _CONSTRAINT_KW_STRONG,
    _DIALECT_MYSQL,
    _DIALECT_PG,
    _DIALECT_SQLITE,
    _ORM_HINTS,
    _RE_COLUMN,
    _RE_CREATE_TABLE,
    _RE_INDEX_CLAUSE_TAIL,
    _SQL_SUFFIXES,
    _SQLITE_SUFFIXES,
    _detect_dialect,
    _extract_table_body,
    _is_constraint_segment,
    _parse_create_table_columns,
    _scan_create_table_text,
    _scan_sql_file,
    _split_columns,
)
from codev_platform.plugins.builtin.sql.orm import (
    _RE_CAMEL_BOUNDARY,
    _core_column_type,
    _django_db_table,
    _is_models_model_base,
    _sa_column_type,
    _scan_python_core_tables,
    _scan_python_orm,
    _snake_case,
)
from codev_platform.plugins.builtin.sql.core import (
    _CORE_JOIN_METHOD,
    _CORE_READ_FN,
    _CORE_UPDATE_FN,
    _CORE_UPSERT_METHOD,
    _CORE_WRITE_FN,
    _JAVA_KW_DML,
    _MYBATIS_PLUS_CONF,
    _RE_BASEMAPPER,
    _RE_DML_DELETE,
    _RE_DML_FROM,
    _RE_DML_INSERT,
    _RE_DML_JOIN,
    _RE_DML_UPDATE,
    _RE_ENTITY_CLASS,
    _RE_HAS_SQL,
    _RE_IS_SQL,
    _RE_JAVA_SQL_ANN,
    _RE_JAVA_STR,
    _RE_TABLENAME,
    _c_chain_root,
    _func_ranges,
    _java_method_after,
    _local_table_aliases,
    _owner_func,
    _qa_paren,
    _resolve_table_arg,
    _scan_core_dml_in_func,
    _scan_entity_tables,
    _scan_java_dml,
    _scan_mybatis_plus,
    _scan_python_core_dml,
    _scan_python_core_table_vars,
    _scan_python_dml,
    _sql_table_access,
    _upsert_outer_table,
)
from codev_platform.plugins.builtin.sql.xml_mapper import (
    _scan_xml_mapper,
    is_mybatis_mapper,
)
from codev_platform.plugins.builtin.sql.hbm import (
    _scan_hbm,
    is_hbm_mapping,
)

logger = logging.getLogger(__name__)

PLUGIN_NAME = "builtin.sql"


class SqlPlugin(AnalyzerPlugin):
    """SQL / 数据库栈 -> db_table / db_column 节点 + contains 边 (方言走 meta["dialect"])。"""

    name = PLUGIN_NAME
    version = "0.1.0"
    prov_source = ProvSource.REGEX.value  # SQL DDL/DML 正则解析(精度由边 confidence 承载)
    produces = (NodeKind.DB_TABLE.value, NodeKind.DB_COLUMN.value,
                NodeKind.BACKEND_FUNCTION.value)

    def detect(self, repo_path: Path) -> bool:
        repo = Path(repo_path)
        # 1) 有 .sql 文件 (最强信号: CREATE TABLE / 迁移脚本在这里)。
        if _stack_scan._iter_files(repo, _SQL_SUFFIXES):
            return True
        # 2) 有嵌入式 sqlite 库文件 (.sqlite / .db / .sqlite3)。
        if _stack_scan._iter_files(repo, _SQLITE_SUFFIXES):
            return True
        # 3) Python 源迹象: ORM (SQLAlchemy / Django) 或内嵌 CREATE TABLE 字符串
        #    (codev 主用 conn.execute("CREATE TABLE ...") / executescript 建表)。
        for f in _stack_scan._iter_files(repo, (".py",)):
            try:
                text = f.read_text(encoding="utf-8")
            except OSError:
                continue
            if any(hint in text for hint in _ORM_HINTS):
                return True
            if _RE_CREATE_TABLE.search(text):
                return True
        # 4) Java MyBatis 注解 SQL (@Select/@Insert/... raw SQL) 也算 DB 栈。
        for f in _stack_scan._iter_files(repo, (".java",)):
            try:
                text = f.read_text(encoding="utf-8")
            except OSError:
                continue
            if _RE_JAVA_SQL_ANN.search(text):
                return True
        # 5) MyBatis XML Mapper (*.xml 含 <mapper>) 或 Hibernate HBM (*.xml 含 <hibernate-mapping>) 也算 DB 栈。
        for f in _stack_scan._iter_files(repo, (".xml",)):
            try:
                text = f.read_text(encoding="utf-8")
            except OSError:
                continue
            if is_mybatis_mapper(text) or is_hbm_mapping(text):
                return True
        return False

    def analyze(self, repo_path: Path, project_id: str) -> AnalyzerResult:
        repo = Path(repo_path)
        result = AnalyzerResult(plugin=PLUGIN_NAME)
        # 跨源去重: 同 table/column node id (按表名/列名归一) 只保留首次出现 (.sql 优先,
        # 再 python-ddl, 再 ORM)。id 已含表/列名 lower, 故天然合并多源同名表。
        seen_nodes: set[str] = set()
        seen_edges: set[tuple[str, str, str]] = set()

        def _absorb(nodes: list[GraphNode], edges: list[GraphEdge]) -> None:
            for n in nodes:
                if n.id in seen_nodes:
                    continue
                seen_nodes.add(n.id)
                result.nodes.append(n)
            for e in edges:
                key = (e.source, e.target, e.kind)
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                result.edges.append(e)

        # Pass 1: .sql 文件里的 CREATE TABLE。
        for f in _stack_scan._iter_files(repo, _SQL_SUFFIXES):
            try:
                text = f.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("read fail %s: %s", f, exc)
                continue
            rel = _stack_scan._rel(f, repo)
            if _is_test_path(rel):  # 测试夹具 .sql 不当生产表
                continue
            dialect = _detect_dialect(text, f.name)
            _absorb(*_scan_sql_file(text, rel, dialect, project_id))

        # Pass 2 + 3: .py 源 —— 内嵌 CREATE TABLE 字符串 + SQLAlchemy/Django ORM。
        # 同时缓存 (rel, src) 供 Pass 4 复用 (不重复读盘)。
        # core_table_vars: 全局 `<var> = Table("x")` -> {var -> 表名}, 供 Core DML 别名解析
        # (Pass 4.5)。跨文件汇总 —— tables.py 定义 orgs, *_pg.py 里 o=tables.orgs 才能解析。
        py_srcs: list[tuple[str, str]] = []
        core_table_vars: dict[str, str] = {}
        for f in _stack_scan._iter_files(repo, (".py",)):
            try:
                src = f.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("read fail %s: %s", f, exc)
                continue
            rel = _stack_scan._rel(f, repo)
            if _is_test_path(rel):  # 测试夹具 .py 不当生产表/访问源 (治幽灵表 + 幽灵 reader)
                continue
            py_srcs.append((rel, src))
            if "Table(" in src:  # 廉价短路: 仅含 Table( 的文件参与全局别名预扫。
                core_table_vars.update(_scan_python_core_table_vars(src))
            # Pass 2: python-ddl (字符串字面量里的 CREATE TABLE; 正则直扫全文)。
            if _RE_CREATE_TABLE.search(src):
                dialect = _detect_dialect(src, f.name)
                _absorb(*_scan_create_table_text(
                    src, rel, dialect, project_id,
                    language="python", source="python-ddl",
                ))
            # Pass 3: ORM declarative (AST; 仅当文件含 ORM 迹象时才解析, 省 AST 开销)。
            if any(hint in src for hint in _ORM_HINTS):
                _absorb(*_scan_python_orm(src, rel, project_id))
            # Pass 3.5: SQLAlchemy Core imperative Table() (本仓 web/db/tables.py 权威表来源,
            # declarative 扫描漏它)。廉价短路: 仅含 'Table(' 的文件才 AST 解析。
            if "Table(" in src:
                _absorb(*_scan_python_core_tables(src, rel, project_id))

        # Pass 3.6: Hibernate HBM XML 表定义 (<class>/<joined-subclass> table=... -> db_table)。
        # 在 DDL 定义相 (known_tables 计算前): 让后续 Java DML/MyBatis 访问边能连到真 hbm 表而非
        # 建 inferred stub。纯 Hibernate 仓 (无 .sql/注解, 如 ideas-v2) 的整个 DB 层全靠这一 pass。
        for f in _stack_scan._iter_files(repo, (".xml",)):
            try:
                src = f.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("read fail %s: %s", f, exc)
                continue
            rel = _stack_scan._rel(f, repo)
            if _is_test_path(rel) or not is_hbm_mapping(src):
                continue
            _absorb(*_scan_hbm(src, rel, project_id))

        # Pass 4: .py DML 读写血缘 —— **必须在所有 DDL 之后** (known_tables 完整, 才能
        # 区分"已定义表 (连边)"与"未定义表 (建 inferred stub)", 不会用 stub 覆盖真表)。
        known_tables = {
            n.name.lower() for n in result.nodes if n.kind == NodeKind.DB_TABLE.value
        }
        for rel, src in py_srcs:
            if not _RE_HAS_SQL.search(src):  # 廉价短路: 无 DML 动词文件跳过 AST。
                continue
            _absorb(*_scan_python_dml(src, rel, project_id, known_tables))

        # Pass 4.5: .py SQLAlchemy Core DML builder 读写血缘 (select/insert/update/delete)。
        # 也在 known_tables 完整后跑 (同 Pass 4 理由)。廉价短路: 文件含 builder 调用形态才解析。
        for rel, src in py_srcs:
            if not (
                "select(" in src or "insert(" in src
                or "update(" in src or "delete(" in src
            ):
                continue
            _absorb(*_scan_python_core_dml(
                src, rel, project_id, known_tables, core_table_vars
            ))

        # Pass 4b/4c: .java —— 注解 SQL DML + MyBatis-Plus BaseMapper CRUD 表访问血缘。
        java_srcs: list[tuple[str, str]] = []
        for f in _stack_scan._iter_files(repo, (".java",)):
            try:
                src = f.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("read fail %s: %s", f, exc)
                continue
            rel = _stack_scan._rel(f, repo)
            if _is_test_path(rel):  # 测试夹具 .java 不当生产表/访问源
                continue
            java_srcs.append((rel, src))

        # 4b: 注解 SQL (@Select/@Insert/@Update/@Delete raw SQL)。
        for rel, src in java_srcs:
            if _RE_JAVA_SQL_ANN.search(src):
                _absorb(*_scan_java_dml(src, rel, project_id, known_tables))

        # 4c: MyBatis-Plus BaseMapper<Entity> -> @TableName 表 (隐式 CRUD, 粗粒度读写)。
        entity_table = _scan_entity_tables(java_srcs)
        if entity_table:
            _absorb(*_scan_mybatis_plus(
                java_srcs, project_id, known_tables, entity_table
            ))

        # Pass 4d: MyBatis XML Mapper (*.xml 的 <select>/<insert>/<update>/<delete> SQL)。
        # 第三种 Java 表访问写法 (注解 4b / Plus 4c 之外); 去动态标签后走同款 _sql_table_access。
        for f in _stack_scan._iter_files(repo, (".xml",)):
            try:
                src = f.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("read fail %s: %s", f, exc)
                continue
            rel = _stack_scan._rel(f, repo)
            if _is_test_path(rel) or not is_mybatis_mapper(src):
                continue
            _absorb(*_scan_xml_mapper(src, rel, project_id, known_tables))

        return result
