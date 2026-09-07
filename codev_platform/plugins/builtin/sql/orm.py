"""builtin.sql.orm — Python ORM 表定义扫描 (SQLAlchemy declarative / Core Table() / Django)。

AST 扫 .py 里的 ORM 模型 -> db_table / db_column 节点。三类源:
  - SQLAlchemy declarative: `class X: __tablename__ = "t"; col = Column(...)`。
  - SQLAlchemy Core imperative: `t = Table("name", metadata, Column("c", Type, ...))`。
  - Django: `class X(models.Model): f = models.XxxField(...)`。
第一版轻量: 只解析类体/调用顶层简单形态, 不展开 mixin / 抽象基类继承的列。
"""
from __future__ import annotations

import ast
import logging
import re

from codev_platform.graph.schema import GraphEdge, GraphNode
from codev_platform.plugins.builtin.sql._common import (
    _call_attr_chain,
    _emit_table_nodes,
    _plausible_table,
    _str_const,
)

logger = logging.getLogger(__name__)


# CamelCase 模型名 -> snake_case 表名 (Django 默认 db_table 规则: app 前缀此处不可知,
# 只做模型名本身的 snake 化, 第一版不拼 app_label)。
_RE_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _snake_case(name: str) -> str:
    """CamelCase -> snake_case (Django 模型名默认表名近似)。"""
    return _RE_CAMEL_BOUNDARY.sub("_", name).lower()


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


def _core_column_type(call: ast.Call) -> str | None:
    """SQLAlchemy Core Column("name", Type, ...) 的类型名 = 第二个位置实参 (第一个是列名串)。"""
    if len(call.args) >= 2:
        t = call.args[1]
        if isinstance(t, ast.Call):
            return _call_attr_chain(t)
        if isinstance(t, ast.Name):
            return t.id
        if isinstance(t, ast.Attribute):
            return t.attr
    return None


def _scan_python_core_tables(
    src: str, rel: str, project_id: str
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """AST 扫 SQLAlchemy Core **imperative** Table() 定义 -> db_table / db_column。

    形态: `<var> = Table("orgs", metadata, Column("org_id", Text, ...), Column(...), ...)`。
    表名 = 第一个 str 位置实参; 列名 = 每个 Column(...) 的第一个 str 位置实参 (Core 列名是
    显式串, 区别于 declarative 的 `col = Column(Type)` 用属性名)。source=sqlalchemy-core,
    是本仓 web/db/tables.py 的权威表来源 (替代从 DML 推断的无源桩)。
    """
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        logger.warning("python core-table parse fail %s: %s", rel, exc)
        return nodes, edges

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        if _call_attr_chain(node.value) != "Table" or not node.value.args:
            continue
        table = _str_const(node.value.args[0])
        if not table or not _plausible_table(table.lower()):
            continue
        cols: list[tuple[str, str | None]] = []
        for a in node.value.args[1:]:
            if isinstance(a, ast.Call) and _call_attr_chain(a) == "Column" and a.args:
                cname = _str_const(a.args[0])
                if cname:
                    cols.append((cname, _core_column_type(a)))
        t_nodes, t_edges = _emit_table_nodes(
            table, cols, rel=rel, line=node.lineno, language="python",
            project_id=project_id,
            table_meta={"dialect": "unknown", "source": "sqlalchemy-core"},
            col_meta={"dialect": "unknown", "source": "sqlalchemy-core"},
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
