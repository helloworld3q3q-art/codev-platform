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
  - db_table --defines_column--> db_column 边 (裸字符串 kind, 对齐 cross_link 适配器
    的 defines_column 约定; 原始 rel 同时写 meta["cross_link_rel"], 保证 Flyway 经
    cross_link 产的边与本插件直扫 SQL 产的边同 kind, 跨插件可链接)。
  - 方言推断写进 db_table 节点 meta["dialect"] (sqlite / mysql / postgres / unknown):
    文件名后缀 (*.sqlite.sql) / 内容特征 (AUTO_INCREMENT=mysql, SERIAL=pg, AUTOINCREMENT=sqlite)。

第一版轻量正则解析 (plan 允许"不追求覆盖所有写法"): 只解析最常见的
`CREATE TABLE [IF NOT EXISTS] name (col type ..., ...)` 形态。约束 / 索引 / 复杂表达式
列暂不深解析, 但保证产出非空且 schema 合法, 跨插件 node id 可链接。

node id 统一 "<project_id>:<kind>:<stable-key>" (与 _stack_scan / cross_link 同构):
  - db_table:  "<pid>:db_table:<table_name_lower>"
  - db_column: "<pid>:db_column:<table_name_lower>.<col_name_lower>"
表名做大小写归一 (lower), 保证 endpoint->table 链路里 reads/writes_table 边能命中。
"""
from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

from codev_platform.graph.schema import (
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from codev_platform.plugins.base import AnalyzerPlugin
from codev_platform.plugins.builtin import _stack_scan

logger = logging.getLogger(__name__)

PLUGIN_NAME = "builtin.sql"

# DB 定义关系边 kind: 统一 EdgeKind 暂无精确枚举对应, 沿用 cross_link 适配器
# (graph/adapters/cross_link.py) 既定的裸字符串约定, 保证两个 DB 来源 (Flyway 经
# cross_link / 本插件直接扫 SQL) 产同一 kind, 跨插件可链接。原始值同时写进
# meta["cross_link_rel"] 留底 (与适配器同范式)。
_REL_DEFINES_COLUMN = "defines_column"

# SQL 文件后缀 (CREATE TABLE / 迁移脚本所在)。
_SQL_SUFFIXES = (".sql",)
# 嵌入式数据库文件后缀 (sqlite 系) —— 仅用于 detect (有库文件即说明用了 SQL DB)。
_SQLITE_SUFFIXES = (".sqlite", ".sqlite3", ".db")

# CREATE TABLE [IF NOT EXISTS] [schema.]name ( ... ) —— 捕获表名 + 括号内列定义体。
# 表名允许被反引号 / 双引号 / 方括号包裹 (mysql / pg / mssql 各家引号); 可带 schema 前缀。
# 用 re.ASCII 约束 \w 仅匹配 ASCII (SQL 标识符不含 CJK), 防内嵌 DDL 扫描误命中 .py 注释/
# 文档串里 "CREATE TABLE <中文> (" 这类非 DDL 文本 (\w 默认含 Unicode 会吞中文当表名)。
_RE_CREATE_TABLE = re.compile(
    r"""create\s+table\s+(?:if\s+not\s+exists\s+)?"""
    r"""(?P<name>[`"\[\]\w.]+)\s*\(""",
    re.IGNORECASE | re.ASCII,
)

# 列定义行起始: 列名 (可带引号) + 类型 token。过滤掉表级约束行
# (PRIMARY KEY / FOREIGN KEY / CONSTRAINT / UNIQUE / KEY / INDEX / CHECK)。
_RE_COLUMN = re.compile(
    r"""^\s*(?P<col>`[^`]+`|"[^"]+"|\[[^\]]+\]|\w+)\s+(?P<type>[A-Za-z]\w*)""",
)
# 表级约束行首关键字。分两档:
#   strong = 永远是约束子句的词 (这些词作裸列名极罕见, 真要用须加引号 -> 走列正则的
#            引号分支, 不会落到这里的裸 token 判定)。
#   ambig  = 同时常作真实列名的词 (key / index 是高频业务列名)。仅当其后跟 (cols)
#            分组 (如 `KEY idx_name (a,b)` / `INDEX (a)`) 才判为索引子句, 否则 (如
#            `key INTEGER`) 当真实列处理。修 Bug1: 旧实现把列名 key/index 误当约束丢弃。
_CONSTRAINT_KW_STRONG = frozenset(
    {"primary", "foreign", "constraint", "unique", "check", "fulltext", "spatial"}
)
_CONSTRAINT_KW_AMBIG = frozenset({"key", "index"})


# 索引子句剩余部分形态: 关键字后直接 `(cols)` 或 `idx_name (cols)`
#   —— 关键标志是 '(' 前有空白 (索引名/关键字与列表之间), 或 '(' 紧跟关键字。
# 对比真实列 `key VARCHAR(32)`: 类型与精度括号间**无空白** (`VARCHAR(`), 故不命中。
_RE_INDEX_CLAUSE_TAIL = re.compile(r"""^\s*(?:\(|\w+\s+\()""")


def _is_constraint_segment(seg: str, first_token: str) -> bool:
    """判断列/约束括号体里的一个片段是否为表级约束行 (而非真实列定义)。"""
    low = first_token.lower()
    if low in _CONSTRAINT_KW_STRONG:
        return True
    if low in _CONSTRAINT_KW_AMBIG:
        # 仅 `KEY (cols)` / `KEY idx_name (cols)` / `INDEX (cols)` 这类索引子句判为约束;
        # `key VARCHAR(32)` / `index INTEGER` 这类真实列不命中 (类型精度括号前无空白)。
        tail = seg.lstrip()[len(first_token):]
        return bool(_RE_INDEX_CLAUSE_TAIL.match(tail))
    return False

# 方言特征字面量 (内容级嗅探)。
_DIALECT_MYSQL = re.compile(r"auto_increment|engine\s*=|unsigned\b", re.IGNORECASE)
_DIALECT_PG = re.compile(
    r"\bserial\b|\bbigserial\b|::\w+|\bjsonb\b|nextval\s*\(", re.IGNORECASE
)
_DIALECT_SQLITE = re.compile(r"\bautoincrement\b|without\s+rowid", re.IGNORECASE)

# ORM 迹象 (detect 用): SQLAlchemy / Django / Flyway。
_ORM_HINTS = (
    "declarative_base",
    "sqlalchemy",
    "__tablename__",
    "models.Model",
    "db.Model",
)


def _unquote(name: str) -> str:
    """去掉表名 / 列名外层引号 (反引号 / 双引号 / 方括号) 并取末段 (剥 schema 前缀)。"""
    name = name.strip().strip("`\"[]")
    if "." in name:
        name = name.split(".")[-1].strip("`\"[]")
    return name


def _detect_dialect(text: str, file_name: str) -> str:
    """方言推断: 文件名后缀优先 (*.sqlite.sql), 否则按内容特征字面量。"""
    low_name = file_name.lower()
    if ".sqlite" in low_name or ".sqlite3" in low_name:
        return "sqlite"
    if "mysql" in low_name:
        return "mysql"
    if "postgres" in low_name or "postgresql" in low_name:
        return "postgres"
    # 保守: 仅当出现独立 pg 段 (被 . _ - 包围) 时按 pg, 避免误命中 "page" 等。
    if re.search(r"(?:^|[._-])pg(?:[._-]|$)", low_name):
        return "postgres"
    if _DIALECT_PG.search(text):
        return "postgres"
    if _DIALECT_MYSQL.search(text):
        return "mysql"
    if _DIALECT_SQLITE.search(text):
        return "sqlite"
    return "unknown"


def _split_columns(body: str) -> list[str]:
    """把 CREATE TABLE 括号体按顶层逗号切成列 / 约束定义片段。

    尊重嵌套括号 (DECIMAL(10,2) 内部逗号不切), 且尊重字符串/引号标识符字面量
    ('a,b' / "x,y" / `c,d` 内部逗号不切)。修 Bug2: 旧实现只数括号不识字符串,
    `note TEXT DEFAULT 'a,b'` 的逗号在 depth 0 被误切, 把一列拆成两段垃圾列。
    """
    segments: list[str] = []
    depth = 0
    buf: list[str] = []
    quote: str | None = None
    for ch in body:
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            buf.append(ch)
        elif ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            segments.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        segments.append("".join(buf))
    return segments


def _extract_table_body(text: str, open_paren_idx: int) -> str | None:
    """从 CREATE TABLE 的 '(' 位置起, 匹配到配对 ')' 之间的括号体 (含嵌套)。"""
    depth = 0
    for i in range(open_paren_idx, len(text)):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren_idx + 1:i]
    return None


def _emit_table_nodes(
    table: str,
    columns: list[tuple[str, str | None]],
    *,
    rel: str,
    line: int,
    language: str,
    project_id: str,
    table_meta: dict,
    col_meta: dict,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """统一产 db_table + db_column 节点 + defines_column 边 (三种源共用的出口)。

    columns: (col_name, col_type|None) 列表。语义/id 形态与 .sql 路径完全一致,
    保证跨源 (sql / python-ddl / sqlalchemy / django) 同 table/column id 可去重 + 链接。
    """
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    table_key = table.lower()
    table_id = f"{project_id}:db_table:{table_key}"
    nodes.append(
        GraphNode(
            id=table_id,
            kind=NodeKind.DB_TABLE.value,
            name=table,
            project_id=project_id,
            file=rel,
            line=line,
            language=language,
            meta=dict(table_meta),
        )
    )
    seen_cols: set[str] = set()
    for col_name, col_type in columns:
        if not col_name:
            continue
        col_key = col_name.lower()
        if col_key in seen_cols:  # 同表同列名 (源内重复) 去重。
            continue
        seen_cols.add(col_key)
        col_id = f"{project_id}:db_column:{table_key}.{col_key}"
        meta = dict(col_meta)
        meta["table"] = table
        if col_type is not None:
            meta["col_type"] = col_type
        nodes.append(
            GraphNode(
                id=col_id,
                kind=NodeKind.DB_COLUMN.value,
                name=col_name,
                project_id=project_id,
                file=rel,
                line=line,
                language=language,
                meta=meta,
            )
        )
        edges.append(
            GraphEdge(
                source=table_id,
                target=col_id,
                kind=_REL_DEFINES_COLUMN,
                meta={
                    "cross_link_rel": _REL_DEFINES_COLUMN,
                    "evidence": f"column {col_name} of {table}",
                },
            )
        )
    return nodes, edges


def _parse_create_table_columns(body: str) -> list[tuple[str, str | None]]:
    """把 CREATE TABLE 括号体解析成 (col_name, col_type) 列表 (排除表级约束行)。

    .sql 路径与 python-ddl 路径共用 —— 列识别 / 约束过滤逻辑单一真值源。
    """
    out: list[tuple[str, str | None]] = []
    for seg in _split_columns(body):
        cm = _RE_COLUMN.match(seg)
        if not cm:
            continue
        col_raw = cm.group("col")
        first_token = _unquote(col_raw)
        if not first_token:
            continue
        # 过滤表级约束行 (PRIMARY KEY / FOREIGN KEY / CONSTRAINT / ...);
        # 引号包裹的列名 (col_raw 带引号) 一律视为真实列, 不做约束关键字判定。
        if col_raw == first_token and _is_constraint_segment(seg, first_token):
            continue
        out.append((first_token, cm.group("type")))
    return out


def _scan_create_table_text(
    text: str, rel: str, dialect: str, project_id: str, *,
    language: str = "sql", source: str = "sql",
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """扫一段文本里所有 `CREATE TABLE ... (...)` -> db_table / db_column 节点 + 边。

    对 .sql 文件全文 / .py 文件全文 (内嵌 DDL 在字符串字面量里) 一视同仁:
    正则在哪种文本里匹配到 CREATE TABLE 就抽哪个。复用同一列解析器 + 节点出口。
    """
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    for m in _RE_CREATE_TABLE.finditer(text):
        table = _unquote(m.group("name"))
        if not table:
            continue
        # '(' 紧跟在匹配末尾前一位 (正则以 '(' 结束)。
        open_idx = m.end() - 1
        body = _extract_table_body(text, open_idx)
        if body is None:
            continue
        line = text.count("\n", 0, m.start()) + 1
        columns = _parse_create_table_columns(body)
        t_nodes, t_edges = _emit_table_nodes(
            table, columns,
            rel=rel, line=line, language=language, project_id=project_id,
            table_meta={"dialect": dialect, "source": source},
            col_meta={"dialect": dialect, "source": source},
        )
        nodes.extend(t_nodes)
        edges.extend(t_edges)
    return nodes, edges


# 向后兼容别名: 旧名 _scan_sql_file 等价新 _scan_create_table_text 的 .sql 默认配置。
def _scan_sql_file(
    text: str, rel: str, dialect: str, project_id: str
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """扫单个 SQL 文件文本里的 CREATE TABLE -> db_table / db_column 节点 + 边。"""
    return _scan_create_table_text(
        text, rel, dialect, project_id, language="sql", source="sql"
    )


# ============================ Python 源 (ORM) ============================

# CamelCase 模型名 -> snake_case 表名 (Django 默认 db_table 规则: app 前缀此处不可知,
# 只做模型名本身的 snake 化, 第一版不拼 app_label)。
_RE_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _snake_case(name: str) -> str:
    """CamelCase -> snake_case (Django 模型名默认表名近似)。"""
    return _RE_CAMEL_BOUNDARY.sub("_", name).lower()


def _str_const(node: ast.expr) -> str | None:
    """取 ast 字符串常量值, 非字符串常量返回 None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _call_attr_chain(call: ast.Call) -> str | None:
    """取 Call 的被调名最后一段 (Column / models.CharField -> 'Column' / 'CharField')。"""
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _is_models_model_base(bases: list[ast.expr]) -> bool:
    """判断基类列表是否含 Django models.Model (或裸 Model 习惯写法)。"""
    for b in bases:
        if isinstance(b, ast.Attribute) and b.attr == "Model":
            base_obj = b.value
            if isinstance(base_obj, ast.Name) and base_obj.id == "models":
                return True
    return False


def _scan_python_orm(
    src: str, rel: str, project_id: str
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """AST 扫 .py 里的 SQLAlchemy declarative / Django Model -> db_table / db_column。

    - SQLAlchemy: `class X(...): __tablename__ = "t"; col = Column(Type, ...)`
      -> table = __tablename__ 值, columns = 赋值为 Column(...) 的属性名。source=sqlalchemy。
    - Django: `class X(models.Model): f = models.XxxField(...)`
      -> table = snake(模型名), columns = 赋值为 models.*Field(...) 的属性名。source=django。
    第一版轻量: 只解析类体顶层简单赋值, 不展开 mixin / 抽象基类继承的列。
    """
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        logger.warning("python orm parse fail %s: %s", rel, exc)
        return nodes, edges

    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue

        tablename: str | None = None
        sa_cols: list[tuple[str, str | None]] = []
        dj_cols: list[tuple[str, str | None]] = []
        for stmt in cls.body:
            if not isinstance(stmt, ast.Assign):
                continue
            # 取赋值左侧第一个简单名 (col = ... / __tablename__ = ...)。
            target = stmt.targets[0] if stmt.targets else None
            if not isinstance(target, ast.Name):
                continue
            attr = target.id
            if attr == "__tablename__":
                tablename = _str_const(stmt.value)
                continue
            if not isinstance(stmt.value, ast.Call):
                continue
            callee = _call_attr_chain(stmt.value)
            if callee == "Column":  # SQLAlchemy 列。
                sa_cols.append((attr, _sa_column_type(stmt.value)))
            elif callee and callee.endswith("Field"):  # Django 字段。
                dj_cols.append((attr, callee))

        # SQLAlchemy: 必须有 __tablename__ 才能确定表名。
        if tablename and sa_cols:
            t_nodes, t_edges = _emit_table_nodes(
                tablename, sa_cols,
                rel=rel, line=cls.lineno, language="python",
                project_id=project_id,
                table_meta={"dialect": "unknown", "source": "sqlalchemy"},
                col_meta={"dialect": "unknown", "source": "sqlalchemy"},
            )
            nodes.extend(t_nodes)
            edges.extend(t_edges)
            continue

        # Django: 基类是 models.Model 且有 *Field 列 -> 模型名 snake 为表名。
        if dj_cols and _is_models_model_base(cls.bases):
            table = _django_db_table(cls) or _snake_case(cls.name)
            t_nodes, t_edges = _emit_table_nodes(
                table, dj_cols,
                rel=rel, line=cls.lineno, language="python",
                project_id=project_id,
                table_meta={"dialect": "unknown", "source": "django"},
                col_meta={"dialect": "unknown", "source": "django"},
            )
            nodes.extend(t_nodes)
            edges.extend(t_edges)

    return nodes, edges


def _sa_column_type(call: ast.Call) -> str | None:
    """SQLAlchemy Column(Integer, ...) 第一个位置实参的类型名 (尽力, 无则 None)。"""
    for arg in call.args:
        if isinstance(arg, ast.Call):
            return _call_attr_chain(arg)
        if isinstance(arg, ast.Name):
            return arg.id
        if isinstance(arg, ast.Attribute):
            return arg.attr
    return None


def _django_db_table(cls: ast.ClassDef) -> str | None:
    """Django 内部 class Meta: db_table = 'x' 显式表名 (优先于模型名 snake 化)。"""
    for stmt in cls.body:
        if isinstance(stmt, ast.ClassDef) and stmt.name == "Meta":
            for inner in stmt.body:
                if isinstance(inner, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "db_table"
                    for t in inner.targets
                ):
                    return _str_const(inner.value)
    return None


# ============================ Python DML (表读写血缘 P2) ============================
#
# 扫 .py 里 SQL 字符串字面量 (cursor.execute / conn.execute 等的 raw SQL), 把"哪个函数
# 读/写哪张表"连成血缘: 上游 pipeline 写库函数 --writes_table--> 表 --reads_table-->
# 读库函数。这是 unified-graph-lineage 四层血缘里"上游数据 → SQL → 后端"两段的来源。
# 第一版只解析字符串常量里的 raw SQL (openclaw pipeline 主用 %s 参数 + 字面量表名);
# f-string / ORM DML 留后续。

# 一段字符串是否 DML/查询 (动词起手; 排除 CREATE 等 DDL —— DDL 走 Pass2)。
_RE_IS_SQL = re.compile(r"^\s*(?:insert|update|delete|select|replace|with)\b", re.IGNORECASE | re.ASCII)
# 文件级廉价短路 (无下列子串直接跳过 AST 解析)。
_RE_HAS_SQL = re.compile(r"insert\s+into|update\s+\w|delete\s+from|\bselect\b", re.IGNORECASE | re.ASCII)
_RE_DML_INSERT = re.compile(r"\binsert\s+(?:or\s+\w+\s+)?into\s+([`\"\[]?[\w.]+)", re.IGNORECASE | re.ASCII)
_RE_DML_UPDATE = re.compile(r"\bupdate\s+(?:only\s+)?([`\"\[]?[\w.]+)", re.IGNORECASE | re.ASCII)
_RE_DML_DELETE = re.compile(r"\bdelete\s+from\s+([`\"\[]?[\w.]+)", re.IGNORECASE | re.ASCII)
_RE_DML_FROM = re.compile(r"\bfrom\s+([`\"\[]?[\w.]+)", re.IGNORECASE | re.ASCII)
_RE_DML_JOIN = re.compile(r"\bjoin\s+([`\"\[]?[\w.]+)", re.IGNORECASE | re.ASCII)
# FROM 后非真实表的 token (子查询别名 / 关键字)。
_NON_TABLE = frozenset({"select", "dual", "where", "set", "values", "table", "only", "lateral"})


def _plausible_table(t: str) -> bool:
    return bool(t) and t not in _NON_TABLE and not t.isdigit() and len(t) >= 2


def _sql_table_access(sql: str) -> tuple[set[str], set[str]]:
    """从一段 SQL 解析 (写表集, 读表集), 表名 lower 归一。同串里写优先 (reads 去掉写表)。"""
    writes: set[str] = set()
    reads: set[str] = set()
    for rx in (_RE_DML_INSERT, _RE_DML_UPDATE, _RE_DML_DELETE):
        for m in rx.finditer(sql):
            t = _unquote(m.group(1)).lower()
            if _plausible_table(t):
                writes.add(t)
    for rx in (_RE_DML_FROM, _RE_DML_JOIN):
        for m in rx.finditer(sql):
            t = _unquote(m.group(1)).lower()
            if _plausible_table(t):
                reads.add(t)
    reads -= writes
    return writes, reads


def _func_ranges(tree: ast.AST) -> list[tuple[int, int, str]]:
    """收集所有 (异步)函数的 (lineno, end_lineno, name), 用于把 SQL 串归属到最内层函数。"""
    out: list[tuple[int, int, str]] = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((n.lineno, getattr(n, "end_lineno", n.lineno), n.name))
    return out


def _owner_func(ranges: list[tuple[int, int, str]], lineno: int) -> str | None:
    """含 lineno 的最内层函数名 (无则 None = 模块级)。"""
    best: str | None = None
    best_start = -1
    for start, end, name in ranges:
        if start <= lineno <= end and start > best_start:
            best, best_start = name, start
    return best


def _emit_table_access(
    func_id: str,
    writes: set[str],
    reads: set[str],
    project_id: str,
    known_tables: set[str],
    seen_stub: set[str],
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """给定一个函数 + 它读写的表集, 产 reads/writes_table 边 (+ 未定义表的 inferred stub)。

    Python / Java DML 扫描共用 (单一真值源, 不重复实现表访问边逻辑)。
    """
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    access = (
        [(t, EdgeKind.WRITES_TABLE.value) for t in sorted(writes)]
        + [(t, EdgeKind.READS_TABLE.value) for t in sorted(reads)]
    )
    for table, edge_kind in access:
        table_id = f"{project_id}:db_table:{table}"
        if table not in known_tables and table not in seen_stub:
            seen_stub.add(table)
            nodes.append(
                GraphNode(
                    id=table_id,
                    kind=NodeKind.DB_TABLE.value,
                    name=table,
                    project_id=project_id,
                    meta={"source": "dml-inferred", "inferred": True},
                )
            )
        edges.append(
            GraphEdge(
                source=func_id,
                target=table_id,
                kind=edge_kind,
                meta={"evidence": f"{edge_kind} {table}"},
            )
        )
    return nodes, edges


def _scan_python_dml(
    src: str, rel: str, project_id: str, known_tables: set[str]
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """AST 扫 .py 里 SQL 字符串常量 -> backend_function 节点 + reads/writes_table 边。

    known_tables: 已建 db_table 名 lower 集合。命中即连边; 未命中建 inferred stub
    db_table (血缘不断, meta source=dml-inferred), 由 caller 的 _absorb 跨文件去重。
    """
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        logger.warning("python dml parse fail %s: %s", rel, exc)
        return nodes, edges
    ranges = _func_ranges(tree)
    seen_func: set[str] = set()
    seen_stub: set[str] = set()
    for node in ast.walk(tree):
        s = _str_const(node)
        if not s or not _RE_IS_SQL.match(s):
            continue
        writes, reads = _sql_table_access(s)
        if not writes and not reads:
            continue
        lineno = getattr(node, "lineno", 1)
        owner = _owner_func(ranges, lineno) or f"<{rel.rsplit('/', 1)[-1]}>"
        func_id = f"{project_id}:backend_function:{rel}:{owner}"
        if func_id not in seen_func:
            seen_func.add(func_id)
            nodes.append(
                GraphNode(
                    id=func_id,
                    kind=NodeKind.BACKEND_FUNCTION.value,
                    name=owner,
                    project_id=project_id,
                    file=rel,
                    line=lineno,
                    language="python",
                    meta={"db_access": True},
                )
            )
        a_nodes, a_edges = _emit_table_access(
            func_id, writes, reads, project_id, known_tables, seen_stub
        )
        nodes.extend(a_nodes)
        edges.extend(a_edges)
    return nodes, edges


# ============================ Java 注解 SQL (MyBatis) DML ============================
#
# openclaw 等 Java 后端用 MyBatis @Select/@Insert/@Update/@Delete("...SQL...") 注解承载
# raw SQL (无 XML mapper)。扫这些注解里的 SQL 字符串 -> backend_function(language=java)
# 节点 + reads/writes_table 边, 补"表 -> Java 后端读"这一跳 (cross_link 退场后不丢)。
# MyBatis-Plus BaseMapper 的隐式 CRUD (无显式 SQL) 不在本版覆盖范围 (需 @TableName 实体
# 解析, 留后续)。

_RE_JAVA_SQL_ANN = re.compile(r"@(?:Select|Insert|Update|Delete)\s*\(", re.ASCII)
_RE_JAVA_STR = re.compile(r'"([^"]*)"')
_JAVA_KW_DML = frozenset({"if", "for", "while", "switch", "return", "new", "catch"})


def _qa_paren(text: str, open_idx: int) -> tuple[str | None, int]:
    """从 '(' 起取配对 ')' 间内容, 尊重单/双引号 (SQL 串内括号不计)。返回 (内容, 闭括号下标)。"""
    depth = 0
    quote: str | None = None
    for i in range(open_idx, len(text)):
        ch = text[i]
        if quote is not None:
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:i], i
    return None, len(text)


def _java_method_after(text: str, pos: int) -> str | None:
    """注解闭括号之后第一处方法声明的方法名 (跳过修饰符 / 后续注解 / 返回类型)。"""
    window = text[pos:pos + 400]
    for m in re.finditer(r"([A-Za-z_]\w*)\s*\(", window):
        name = m.group(1)
        if name not in _JAVA_KW_DML:
            return name
    return None


def _scan_java_dml(
    src: str, rel: str, project_id: str, known_tables: set[str]
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """扫 .java 里 @Select/@Insert/@Update/@Delete 注解的 SQL -> backend_function + 读写边。"""
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    seen_func: set[str] = set()
    seen_stub: set[str] = set()
    for m in _RE_JAVA_SQL_ANN.finditer(src):
        open_idx = m.end() - 1
        arg, close_idx = _qa_paren(src, open_idx)
        if arg is None:
            continue
        # 注解参数里所有字符串字面量拼成完整 SQL (MyBatis 允许 {"line1","line2"})。
        sql = " ".join(_RE_JAVA_STR.findall(arg))
        if not sql.strip():
            continue
        writes, reads = _sql_table_access(sql)
        if not writes and not reads:
            continue
        line = src.count("\n", 0, m.start()) + 1
        method = _java_method_after(src, close_idx) or f"<{rel.rsplit('/', 1)[-1]}>"
        func_id = f"{project_id}:backend_function:{rel}:{method}"
        if func_id not in seen_func:
            seen_func.add(func_id)
            nodes.append(
                GraphNode(
                    id=func_id,
                    kind=NodeKind.BACKEND_FUNCTION.value,
                    name=method,
                    project_id=project_id,
                    file=rel,
                    line=line,
                    language="java",
                    meta={"db_access": True},
                )
            )
        a_nodes, a_edges = _emit_table_access(
            func_id, writes, reads, project_id, known_tables, seen_stub
        )
        nodes.extend(a_nodes)
        edges.extend(a_edges)
    return nodes, edges


class SqlPlugin(AnalyzerPlugin):
    """SQL / 数据库栈 -> db_table / db_column 节点 + contains 边 (方言走 meta["dialect"])。"""

    name = PLUGIN_NAME
    version = "0.1.0"

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
            dialect = _detect_dialect(text, f.name)
            _absorb(*_scan_sql_file(text, rel, dialect, project_id))

        # Pass 2 + 3: .py 源 —— 内嵌 CREATE TABLE 字符串 + SQLAlchemy/Django ORM。
        # 同时缓存 (rel, src) 供 Pass 4 复用 (不重复读盘)。
        py_srcs: list[tuple[str, str]] = []
        for f in _stack_scan._iter_files(repo, (".py",)):
            try:
                src = f.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("read fail %s: %s", f, exc)
                continue
            rel = _stack_scan._rel(f, repo)
            py_srcs.append((rel, src))
            # Pass 2: python-ddl (字符串字面量里的 CREATE TABLE; 正则直扫全文)。
            if _RE_CREATE_TABLE.search(src):
                dialect = _detect_dialect(src, f.name)
                _absorb(*_scan_create_table_text(
                    src, rel, dialect, project_id,
                    language="python", source="python-ddl",
                ))
            # Pass 3: ORM (AST; 仅当文件含 ORM 迹象时才解析, 省 AST 开销)。
            if any(hint in src for hint in _ORM_HINTS):
                _absorb(*_scan_python_orm(src, rel, project_id))

        # Pass 4: .py DML 读写血缘 —— **必须在所有 DDL 之后** (known_tables 完整, 才能
        # 区分"已定义表 (连边)"与"未定义表 (建 inferred stub)", 不会用 stub 覆盖真表)。
        known_tables = {
            n.name.lower() for n in result.nodes if n.kind == NodeKind.DB_TABLE.value
        }
        for rel, src in py_srcs:
            if not _RE_HAS_SQL.search(src):  # 廉价短路: 无 DML 动词文件跳过 AST。
                continue
            _absorb(*_scan_python_dml(src, rel, project_id, known_tables))

        # Pass 4b: .java 注解 SQL DML (MyBatis @Select/@Insert/... raw SQL) -> 表读写血缘。
        for f in _stack_scan._iter_files(repo, (".java",)):
            try:
                src = f.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("read fail %s: %s", f, exc)
                continue
            if not _RE_JAVA_SQL_ANN.search(src):  # 廉价短路: 无 SQL 注解文件跳过。
                continue
            rel = _stack_scan._rel(f, repo)
            _absorb(*_scan_java_dml(src, rel, project_id, known_tables))

        return result
