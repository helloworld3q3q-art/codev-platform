"""builtin.sql.ddl — SQL CREATE TABLE DDL 解析 (.sql 文件 + .py 内嵌字符串字面量)。

扫一段文本里所有 `CREATE TABLE ... (...)` -> db_table / db_column 节点 + defines_column 边。
方言推断 (sqlite / mysql / postgres) 也在此 (文件名后缀 + 内容特征字面量)。列识别 / 约束
过滤是 .sql 路径与 python-ddl 路径的单一真值源。
"""
from __future__ import annotations

import re

from codev_platform.graph.schema import GraphEdge, GraphNode
from codev_platform.plugins.builtin.sql._common import (
    _emit_table_nodes,
    _unquote,
)

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
