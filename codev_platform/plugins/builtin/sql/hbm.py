"""builtin.sql 的 Hibernate HBM XML 表定义扫描域 (Pass 3.6)。

补第四种表来源:经典 Hibernate `*.hbm.xml` 映射 (`<class table=>` / `<joined-subclass>` /
`<subclass>` / `<union-subclass>`)。ORM 注解 (JPA @Table) 与 MyBatis 之外, 大量遗留 Java 仓
(如 sample-project-beta: 1068 个 .hbm.xml) 用 HBM XML 映射实体↔表 —— 不扫则该仓**整个 DB 层在统一图谱
里是空的** (实测 sample-project-beta db_table 节点数 = 0), 任何"表/实体影响面"查询必 found:false。

产出对齐既有抽取器 (复用 _emit_table_nodes, 零新节点 kind):
  - 每个带 table= 的映射元素 -> db_table 节点; <property>/<id>/<key>/<many-to-one> -> db_column。
  - **实体类名写进 db_table 节点 meta["entity_class"]** (取 name= 末段, 如 OmsInboundOrder) ——
    人/agent 自然用**实体类名**指代表 (OmsInboundOrder), 而库里是表名 (OMS_INBOUND_ORDER);
    impact._resolve 据此 meta 把类名解析到表节点, 根治"拿类名查影响面 found:false"。

继承: joined-subclass 列落**子表** (OMS_INBOUND_ORDER), 基类公共列落基表 (OMS_ORDER) —— 各
table 元素只取**直接子**的列, 不把嵌套 subclass 的列算给父表 (ElementTree 层级天然区分)。
第一版轻量 (同 ddl/orm 口径): 不展开 <component> 嵌套列 / <set> 集合 (集合列在多端表, 非本表)。
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET

from codev_platform.graph.schema import GraphEdge, GraphNode
from codev_platform.plugins.builtin.sql._common import (
    _emit_table_nodes,
    _plausible_table,
    _unquote,
)

logger = logging.getLogger(__name__)

# 廉价判定 + DOCTYPE 剥离 (HBM 带 <!DOCTYPE hibernate-mapping PUBLIC ... dtd>; ET 不取外部 DTD,
# 但剥掉更稳, 避免个别环境对 DOCTYPE 报错)。
_RE_IS_HBM = re.compile(r"<hibernate-mapping\b", re.IGNORECASE)
_RE_DOCTYPE = re.compile(r"<!DOCTYPE.*?>", re.IGNORECASE | re.DOTALL)

# 定义表的映射元素 (各自有 table= 属性 → 一个 db_table)。
_TABLE_TAGS = frozenset({"class", "joined-subclass", "subclass", "union-subclass"})
# 直接承载列的元素 (column= 属性 或 嵌套 <column name=>)。
_COL_TAGS = frozenset({
    "property", "id", "key", "version", "timestamp", "many-to-one", "key-many-to-one",
    "key-property", "discriminator",
})


def is_hbm_mapping(text: str) -> bool:
    """廉价判定: .xml 是否 Hibernate HBM 映射 (含 <hibernate-mapping 根标签)。detect / analyze 共用。"""
    return bool(_RE_IS_HBM.search(text))


def _localname(tag: str) -> str:
    """剥 ElementTree 命名空间前缀 ({ns}tag -> tag); HBM 通常无 ns, 防御性处理。"""
    return tag.rsplit("}", 1)[-1]


def _columns_of(table_el: ET.Element) -> list[tuple[str, str | None]]:
    """取一个 table 元素的**直接子**列 (不含嵌套 subclass 的列)。

    列来源: 元素的 column= 属性, 或嵌套 <column name=> 子元素 (many-to-one / 复合)。
    嵌套 subclass 标签跳过 (它们各自作为独立 table 元素被处理)。
    """
    cols: list[tuple[str, str | None]] = []
    for child in list(table_el):
        ctag = _localname(child.tag)
        if ctag in _TABLE_TAGS:  # 嵌套 subclass: 它的列归它自己的表, 不算父表
            continue
        if ctag not in _COL_TAGS:
            continue
        ctype = child.get("type")
        col = child.get("column")
        if col:
            cols.append((_unquote(col), ctype))
            continue
        # <property name="x"><column name="X"/></property> 形态 (可多列)
        for col_el in child.findall("column"):
            cname = col_el.get("name")
            if cname:
                cols.append((_unquote(cname), ctype))
    return cols


def _scan_hbm(
    src: str, rel: str, project_id: str
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """扫一个 Hibernate HBM XML -> db_table / db_column 节点 (entity_class 写进表 meta)。

    每个带 table= 的 <class>/<joined-subclass>/<subclass>/<union-subclass> -> 一个 db_table,
    name= 末段作 meta["entity_class"]。复用 _emit_table_nodes, id/语义与其它源一致可去重链接。
    """
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    cleaned = _RE_DOCTYPE.sub("", src)
    try:
        root = ET.fromstring(cleaned)
    except ET.ParseError as exc:
        logger.warning("hbm parse fail %s: %s", rel, exc)
        return nodes, edges

    for el in root.iter():
        if _localname(el.tag) not in _TABLE_TAGS:
            continue
        table = el.get("table")
        if not table:  # <subclass> 单表继承无独立 table: 列共享父表, v1 不另建表节点
            continue
        table = _unquote(table)
        if not _plausible_table(table.lower()):
            continue
        cls = el.get("name") or ""
        entity = cls.rsplit(".", 1)[-1] if cls else None
        table_meta: dict = {"dialect": "unknown", "source": "hibernate-hbm"}
        if entity:
            table_meta["entity_class"] = entity
        t_nodes, t_edges = _emit_table_nodes(
            table, _columns_of(el),
            rel=rel, line=0, language="java", project_id=project_id,
            table_meta=table_meta,
            col_meta={"dialect": "unknown", "source": "hibernate-hbm"},
        )
        nodes.extend(t_nodes)
        edges.extend(t_edges)
    return nodes, edges
