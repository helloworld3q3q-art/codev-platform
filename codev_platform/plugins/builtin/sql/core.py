"""builtin.sql.core — DML 读写血缘扫描 (SQLAlchemy Core / Python raw SQL / Java MyBatis)。

把"哪个函数读/写哪张表"连成血缘: 上游写库函数 --writes_table--> 表 --reads_table-->
读库函数。覆盖四类源:
  - SQLAlchemy Core DML builder (select/insert/update/delete + upsert/join, AST)。
  - Python raw SQL 字符串字面量 (cursor.execute / conn.execute, AST + 正则)。
  - Java MyBatis 注解 SQL (@Select/@Insert/@Update/@Delete raw SQL)。
  - MyBatis-Plus BaseMapper<Entity> 隐式 CRUD (@TableName 两跳, 粗粒度)。
"""
from __future__ import annotations

import ast
import logging
import re

from codev_platform.graph.schema import GraphEdge, GraphNode, NodeKind
from codev_platform.plugins.builtin.sql._common import (
    _call_attr_chain,
    _emit_table_access,
    _plausible_table,
    _str_const,
    _unquote,
)

logger = logging.getLogger(__name__)


# ===================== SQLAlchemy Core DML 读写血缘 (P2) =====================
#
# 本仓 web/repositories/*_pg.py 不写 raw SQL, 全走 Core builder:
#   o = tables.orgs
#   conn.execute(select(o.c.org_id).where(o.c.org_id == code))   # 读 orgs
#   conn.execute(insert(t).values(...))                           # 写 t
#   conn.execute(delete(om).where(...))                           # 写 om
#   _insert(table).values(...).on_conflict_do_update(...)         # upsert = 读+写
#   _upsert_stmt(tables.orgs, ...)                                # 调用方写 orgs (跨函数传表)
# _scan_python_dml 的字符串正则看不到这些 (无 SQL 字面量), 故新加 AST builder 扫描,
# 把"哪个函数读/写哪张表"接进 endpoint->表 链路。两阶段别名: 全局 <var>=Table("x") +
# 函数内 o=tables.x / o=x。表解析不到的实参一律放弃 (不产错边)。

# DML builder 函数名 (callee 末段)。
_CORE_READ_FN = frozenset({"select"})
_CORE_WRITE_FN = frozenset({"insert", "delete"})
_CORE_UPDATE_FN = frozenset({"update"})
# upsert 方法 (链式调在 insert(...) 之上, 受表为外层 insert 的实参)。
_CORE_UPSERT_METHOD = frozenset({"on_conflict_do_update", "on_conflict_do_nothing"})
# join 类方法 (receiver + 首参根变量都是被读的表)。
_CORE_JOIN_METHOD = frozenset({"join", "outerjoin"})


def _scan_python_core_table_vars(src: str) -> dict[str, str]:
    """全局预扫一个 .py: `<var> = Table("realname", ...)` -> {var名 -> 真表名}。

    DML 别名解析的第一阶段 (跨文件汇总): `from db import tables; o = tables.orgs` 里的
    `tables.orgs` 经此映射 (orgs->orgs) 解析。动态表名 `Table(var)` (首参非字符串常量) 放弃。
    """
    out: dict[str, str] = {}
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        if _call_attr_chain(node.value) != "Table" or not node.value.args:
            continue
        table = _str_const(node.value.args[0])
        if not table or not _plausible_table(table.lower()):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name):
                out[tgt.id] = table.lower()
    return out


def _c_chain_root(node: ast.expr) -> str | None:
    """取 `.c` 链根变量名: `o.c.org_id` -> 'o'; `om.c.user_id` -> 'om'。

    形态 Attribute(attr=col, value=Attribute(attr='c', value=Name(root)))。
    不含 `.c` 段 (如裸 Name / 非列引用) -> None。
    """
    cur = node
    while isinstance(cur, ast.Attribute):
        if cur.attr == "c" and isinstance(cur.value, ast.Name):
            return cur.value.id
        cur = cur.value
    return None


def _local_table_aliases(
    func: ast.AST, core_table_vars: dict[str, str]
) -> dict[str, str]:
    """函数体内局部别名: `o = tables.orgs` / `o = orgs` -> {o -> 真表名}。

    经全局 core_table_vars 二次解析:
      - `o = tables.orgs`  : Attribute(attr='orgs') -> core_table_vars['orgs']
      - `o = orgs`         : Name('orgs')           -> core_table_vars['orgs']
    解析不到的赋值跳过 (不引入错别名)。
    """
    local: dict[str, str] = {}
    for stmt in ast.walk(func):
        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
            continue
        tgt = stmt.targets[0]
        if not isinstance(tgt, ast.Name):
            continue
        val = stmt.value
        real: str | None = None
        if isinstance(val, ast.Attribute):  # o = tables.orgs
            real = core_table_vars.get(val.attr)
        elif isinstance(val, ast.Name):  # o = orgs
            real = core_table_vars.get(val.id)
        if real:
            local[tgt.id] = real
    return local


def _resolve_table_arg(
    arg: ast.expr, local: dict[str, str], core_table_vars: dict[str, str]
) -> str | None:
    """把一个实参解析成真表名: Name -> 查 local 再查全局; Attribute(tables.X) -> 查全局。

    解不到返回 None (调用方放弃该实参, 不产错边)。动态表名 (非 Name/Attribute) 也 None。
    """
    if isinstance(arg, ast.Name):
        return local.get(arg.id) or core_table_vars.get(arg.id)
    if isinstance(arg, ast.Attribute):  # tables.orgs
        return core_table_vars.get(arg.attr)
    return None


def _scan_core_dml_in_func(
    func: ast.AST,
    local: dict[str, str],
    core_table_vars: dict[str, str],
) -> tuple[set[str], set[str]]:
    """遍历一个函数体所有 ast.Call, 按 builder 类型累积 (writes, reads) 表集。

    - select(...)            读: 每个实参的 .c 链根 -> 解析表。
    - .select_from/.join/.. : 读: receiver + 首参根变量 -> 解析表。
    - insert(t) / delete(t)  写: 首位置实参解析表。
    - update(t)              写 (updates_table 语义, 这里并入 writes)。
    - on_conflict_*          读+写: 外层 insert(...) 的表 (链式 receiver 上溯)。
    跨函数传表: 任意 Call 的位置实参是 tables.X / 已知表 Name -> 归本函数写 (覆盖 _upsert_stmt
    这类把 tables.orgs 传进去的调用点; helper 内部形参 table 因解不到而自动放弃)。
    """
    writes: set[str] = set()
    reads: set[str] = set()
    for call in ast.walk(func):
        if not isinstance(call, ast.Call):
            continue
        fn = _call_attr_chain(call)
        if fn in _CORE_READ_FN:
            for a in call.args:
                root = _c_chain_root(a)
                if root:
                    t = local.get(root) or core_table_vars.get(root)
                    if t:
                        reads.add(t)
        elif fn in _CORE_JOIN_METHOD and isinstance(call.func, ast.Attribute):
            recv = _resolve_table_arg(call.func.value, local, core_table_vars)
            if recv:
                reads.add(recv)
            if call.args:
                joined = _resolve_table_arg(call.args[0], local, core_table_vars)
                if joined:
                    reads.add(joined)
        elif fn in _CORE_WRITE_FN and call.args:
            t = _resolve_table_arg(call.args[0], local, core_table_vars)
            if t:
                writes.add(t)
        elif fn in _CORE_UPDATE_FN and call.args:
            t = _resolve_table_arg(call.args[0], local, core_table_vars)
            if t:
                writes.add(t)
        elif fn in _CORE_UPSERT_METHOD and isinstance(call.func, ast.Attribute):
            t = _upsert_outer_table(call.func.value, local, core_table_vars)
            if t:
                writes.add(t)
                reads.add(t)
        # 跨函数传表: 调用点把 tables.X / 已知表 Name 作位置实参 -> 本函数写该表。
        # (_upsert_stmt(tables.orgs, ...) / 其它 helper(tables.users, ...))
        if fn not in _CORE_READ_FN and fn not in _CORE_JOIN_METHOD:
            for a in call.args:
                if isinstance(a, ast.Attribute):
                    t = core_table_vars.get(a.attr)
                    if t:
                        writes.add(t)
    reads -= writes
    return writes, reads


def _upsert_outer_table(
    recv: ast.expr, local: dict[str, str], core_table_vars: dict[str, str]
) -> str | None:
    """从 `_insert(table).values(...).on_conflict_*` 的 receiver 链上溯到 insert(...) 的表实参。

    receiver 是一串 Attribute/Call 链; 找到 callee 末段是 insert 的 Call, 取其首位置实参解析表。
    helper 内部的 `_insert(table)` 形参 table 解不到 -> None (放弃, 由调用点 tables.X 兜)。
    """
    cur = recv
    while isinstance(cur, ast.Call):
        fn = _call_attr_chain(cur)
        if fn in _CORE_WRITE_FN and cur.args:  # insert(...)
            return _resolve_table_arg(cur.args[0], local, core_table_vars)
        # 上溯链式 receiver: insert(t).values(...) -> .values 的 receiver 是 insert(t)。
        if isinstance(cur.func, ast.Attribute):
            cur = cur.func.value
        else:
            break
    return None


def _scan_python_core_dml(
    src: str,
    rel: str,
    project_id: str,
    known_tables: set[str],
    core_table_vars: dict[str, str],
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """AST 扫 .py 里 SQLAlchemy Core DML builder -> backend_function 节点 + reads/writes_table 边。

    节点产法 / 函数归属镜像 _scan_python_dml: 每个含 DML builder 的(异步)函数产一个
    backend_function 节点, 出边走 _emit_table_access (confidence=0.9, 区别于 raw SQL 的 1.0)。
    模块级 (函数外) 的 Core DML 罕见, 暂不处理 (本仓全在方法体内)。
    """
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        logger.warning("python core-dml parse fail %s: %s", rel, exc)
        return nodes, edges
    seen_func: set[str] = set()
    seen_stub: set[str] = set()
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        local = _local_table_aliases(func, core_table_vars)
        writes, reads = _scan_core_dml_in_func(func, local, core_table_vars)
        if not writes and not reads:
            continue
        func_id = f"{project_id}:backend_function:{rel}:{func.name}"
        if func_id not in seen_func:
            seen_func.add(func_id)
            nodes.append(
                GraphNode(
                    id=func_id,
                    kind=NodeKind.BACKEND_FUNCTION.value,
                    name=func.name,
                    project_id=project_id,
                    file=rel,
                    line=func.lineno,
                    language="python",
                    meta={"db_access": True, "core_dml": True},
                )
            )
        a_nodes, a_edges = _emit_table_access(
            func_id, writes, reads, project_id, known_tables, seen_stub,
            confidence=0.9,
        )
        nodes.extend(a_nodes)
        edges.extend(a_edges)
    return nodes, edges


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


# ============================ Hibernate HQL ============================
#
# HQL 引用**实体类名** (from OmsInboundOrder), 非 SQL 表名 -> 既有 _sql_table_access (找 SQL
# 表名) 命不中。用 hbm 抽取器产的 entity_class->table 映射把实体名解析回表, 补 Hibernate 仓
# "表 <-> Java 后端" 血缘 (此前 OMS_INBOUND_ORDER 等 hbm 表在图里孤立, find_table_usage usage 空)。
# 归属到字面量所在**类** (粗粒度; 精确方法级交 codegraph Java 调用图)。只对**已知实体**产边
# (entity_to_table 过滤), 故 "select x from Menu" 之类非实体串不误连。

_RE_HQL_HINT = re.compile(r"^\s*(?:from|select|update|delete)\b", re.IGNORECASE | re.ASCII)
_RE_HQL_FROM = re.compile(r"\b(?:from|join)\s+([A-Z]\w+)", re.ASCII)   # 实体类 PascalCase
_RE_HQL_UPDATE = re.compile(r"\bupdate\s+([A-Z]\w+)", re.ASCII)
_RE_HQL_DELETE = re.compile(r"\bdelete\s+from\s+([A-Z]\w+)", re.ASCII)
_RE_JAVA_CLASS_DECL = re.compile(r"\bclass\s+(\w+)", re.ASCII)


def _scan_java_hql(
    src: str, rel: str, project_id: str,
    entity_to_table: dict[str, str], known_tables: set[str],
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """扫 .java 字面量里的 Hibernate HQL -> backend_function(类粒度) + reads/writes_table 边。

    entity_to_table: {实体简名 lower -> 表名 lower}(hbm 抽取器产 db_table.meta.entity_class 汇总)。
    空(非 Hibernate 仓)直接跳。只对**已知实体**产边, 杜绝非 HQL 串误连。confidence 0.8(表访问
    确定, 方法归属粗 -> 不取 1.0)。
    """
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    if not entity_to_table:
        return nodes, edges
    classes = [(mm.start(), mm.group(1)) for mm in _RE_JAVA_CLASS_DECL.finditer(src)]
    seen_func: set[str] = set()
    seen_stub: set[str] = set()
    for m in _RE_JAVA_STR.finditer(src):
        lit = m.group(1)
        if not _RE_HQL_HINT.match(lit):
            continue
        reads: set[str] = set()
        writes: set[str] = set()
        for em in _RE_HQL_FROM.finditer(lit):
            t = entity_to_table.get(em.group(1).lower())
            if t:
                reads.add(t)
        for em in _RE_HQL_UPDATE.finditer(lit):
            t = entity_to_table.get(em.group(1).lower())
            if t:
                writes.add(t)
        for em in _RE_HQL_DELETE.finditer(lit):
            t = entity_to_table.get(em.group(1).lower())
            if t:
                writes.add(t)
        if not reads and not writes:
            continue
        owner: str | None = None
        for pos, name in classes:
            if pos < m.start():
                owner = name
            else:
                break
        owner = owner or f"<{rel.rsplit('/', 1)[-1]}>"
        func_id = f"{project_id}:backend_function:{rel}:{owner}#hql"
        if func_id not in seen_func:
            seen_func.add(func_id)
            nodes.append(
                GraphNode(
                    id=func_id,
                    kind=NodeKind.BACKEND_FUNCTION.value,
                    name=owner,
                    project_id=project_id,
                    file=rel,
                    line=src.count("\n", 0, m.start()) + 1,
                    language="java",
                    meta={"db_access": True, "hql": True},
                )
            )
        a_nodes, a_edges = _emit_table_access(
            func_id, writes, reads, project_id, known_tables, seen_stub, confidence=0.8
        )
        nodes.extend(a_nodes)
        edges.extend(a_edges)
    return nodes, edges


# ============================ MyBatis-Plus BaseMapper CRUD ============================
#
# MyBatis-Plus 的 `interface XxxMapper extends BaseMapper<YyyEntity>` 自动有 CRUD, 无显式
# SQL。表名由实体的 `@TableName("...")` 决定。解析两跳: BaseMapper<Entity> -> Entity 类的
# @TableName -> 表。粗粒度 (mapper 接口同时暴露读+写, 不细分到调用点) -> 读写两条边都建,
# confidence<1 标注推断。补 openclaw 50 个 BaseMapper 走隐式 CRUD 的"表<->Java 后端"血缘。

_RE_TABLENAME = re.compile(r'@TableName\s*\(\s*"([^"]+)"', re.ASCII)
_RE_ENTITY_CLASS = re.compile(r"\bclass\s+(\w+)", re.ASCII)
_RE_BASEMAPPER = re.compile(
    r"\binterface\s+(?P<mapper>\w+)\b[^{]*?extends\s+\w*BaseMapper\s*<\s*(?P<entity>\w+)",
    re.ASCII | re.S,
)
_MYBATIS_PLUS_CONF = 0.6  # BaseMapper CRUD 粗粒度推断, 置信度低于显式 SQL/DDL。


def _scan_entity_tables(java_srcs: list[tuple[str, str]]) -> dict[str, str]:
    """从 @TableName("t") + 紧随的 class X 建 {实体类名 -> 表名}。"""
    out: dict[str, str] = {}
    for _rel, src in java_srcs:
        for m in _RE_TABLENAME.finditer(src):
            table = m.group(1).strip()
            cm = _RE_ENTITY_CLASS.search(src, m.end())
            if cm and table:
                out[cm.group(1)] = table
    return out


def _scan_mybatis_plus(
    java_srcs: list[tuple[str, str]],
    project_id: str,
    known_tables: set[str],
    entity_table: dict[str, str],
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """BaseMapper<Entity> -> @TableName 表; mapper 接口作 backend_function, 读写边 (粗粒度)。"""
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    seen_func: set[str] = set()
    seen_stub: set[str] = set()
    for rel, src in java_srcs:
        for m in _RE_BASEMAPPER.finditer(src):
            mapper = m.group("mapper")
            entity = m.group("entity")
            table = entity_table.get(entity)
            if not table:
                continue
            t = _unquote(table).lower()
            if not _plausible_table(t):
                continue
            line = src.count("\n", 0, m.start()) + 1
            func_id = f"{project_id}:backend_function:{rel}:{mapper}"
            if func_id not in seen_func:
                seen_func.add(func_id)
                nodes.append(
                    GraphNode(
                        id=func_id,
                        kind=NodeKind.BACKEND_FUNCTION.value,
                        name=mapper,
                        project_id=project_id,
                        file=rel,
                        line=line,
                        language="java",
                        meta={"db_access": True, "mybatis_plus": True, "entity": entity},
                    )
                )
            a_nodes, a_edges = _emit_table_access(
                func_id, {t}, {t}, project_id, known_tables, seen_stub,
                confidence=_MYBATIS_PLUS_CONF,
            )
            nodes.extend(a_nodes)
            edges.extend(a_edges)
    return nodes, edges
