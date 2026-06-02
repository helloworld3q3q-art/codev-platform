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

import logging
import re
from pathlib import Path

from codev_platform.graph.schema import (
    AnalyzerResult,
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
_RE_CREATE_TABLE = re.compile(
    r"""create\s+table\s+(?:if\s+not\s+exists\s+)?"""
    r"""(?P<name>[`"\[\]\w.]+)\s*\(""",
    re.IGNORECASE,
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


def _scan_sql_file(
    text: str, rel: str, dialect: str, project_id: str
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """扫单个 SQL 文件文本里的 CREATE TABLE -> db_table / db_column 节点 + contains 边。"""
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    for m in _RE_CREATE_TABLE.finditer(text):
        raw_name = m.group("name")
        table = _unquote(raw_name)
        if not table:
            continue
        # '(' 紧跟在匹配末尾前一位 (正则以 '(' 结束)。
        open_idx = m.end() - 1
        body = _extract_table_body(text, open_idx)
        if body is None:
            continue
        line = text.count("\n", 0, m.start()) + 1
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
                language="sql",
                meta={"dialect": dialect},
            )
        )

        for seg in _split_columns(body):
            cm = _RE_COLUMN.match(seg)
            if not cm:
                continue
            col_raw = cm.group("col")
            first_token = _unquote(col_raw)
            if not first_token:
                continue
            # 过滤表级约束行 (PRIMARY KEY / FOREIGN KEY / CONSTRAINT / ...);
            # 引号包裹的列名 (col_raw 带引号) 一律视为真实列, 不做约束关键字判定
            # (用户显式引用即意图把保留字当列名)。
            if col_raw == first_token and _is_constraint_segment(seg, first_token):
                continue
            col_name = first_token
            col_type = cm.group("type")
            col_key = col_name.lower()
            col_id = f"{project_id}:db_column:{table_key}.{col_key}"
            nodes.append(
                GraphNode(
                    id=col_id,
                    kind=NodeKind.DB_COLUMN.value,
                    name=col_name,
                    project_id=project_id,
                    file=rel,
                    line=line,
                    language="sql",
                    meta={
                        "dialect": dialect,
                        "table": table,
                        "col_type": col_type,
                    },
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
        # 3) ORM 迹象 (Python SQLAlchemy / Django models)。
        for f in _stack_scan._iter_files(repo, (".py",)):
            try:
                text = f.read_text(encoding="utf-8")
            except OSError:
                continue
            if any(hint in text for hint in _ORM_HINTS):
                return True
        return False

    def analyze(self, repo_path: Path, project_id: str) -> AnalyzerResult:
        repo = Path(repo_path)
        result = AnalyzerResult(plugin=PLUGIN_NAME)
        seen_tables: set[str] = set()
        seen_cols: set[str] = set()

        for f in _stack_scan._iter_files(repo, _SQL_SUFFIXES):
            try:
                text = f.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("read fail %s: %s", f, exc)
                continue
            rel = _stack_scan._rel(f, repo)
            dialect = _detect_dialect(text, f.name)
            nodes, edges = _scan_sql_file(text, rel, dialect, project_id)
            for n in nodes:
                if n.kind == NodeKind.DB_TABLE.value:
                    if n.id in seen_tables:
                        continue
                    seen_tables.add(n.id)
                else:  # db_column
                    if n.id in seen_cols:
                        continue
                    seen_cols.add(n.id)
                result.nodes.append(n)
            result.edges.extend(edges)

        return result
