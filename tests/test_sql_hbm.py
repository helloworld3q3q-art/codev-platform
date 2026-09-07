"""builtin.sql Hibernate HBM XML 抽取 (hbm.py) + 实体↔表桥单测。

验:
- <class>/<joined-subclass> table= -> db_table 节点, name= 末段写 meta["entity_class"]。
- 继承列归属正确: 子类列落子表, 基类列不混入 (各 table 元素只取直接子列)。
- many-to-one 嵌套 <column> 取到; <set> 集合不算本表列。
- SqlPlugin.detect + analyze 端到端认 .hbm.xml (纯 Hibernate 仓不再 DB 层全黑)。
"""
from __future__ import annotations

from pathlib import Path

from codev_platform.graph.schema import NodeKind
from codev_platform.plugins.builtin.sql import SqlPlugin
from codev_platform.plugins.builtin.sql.hbm import _scan_hbm, is_hbm_mapping

_HBM = """<?xml version="1.0"?>
<!DOCTYPE hibernate-mapping PUBLIC "-//Hibernate/Hibernate Mapping DTD 3.0//EN"
 "http://hibernate.org/dtd/hibernate-mapping-3.0.dtd">
<hibernate-mapping>
  <class name="com.x.oms.OmsOrder" table="OMS_ORDER" dynamic-update="true">
    <id name="id" column="ORDER_ID" type="long"/>
    <property name="orderNo" column="ORDER_NO" type="string" length="50"/>
    <joined-subclass name="com.x.oms.OmsInboundOrder" table="OMS_INBOUND_ORDER" dynamic-update="true">
      <key column="ORDER_ID"/>
      <property name="carrierName" column="CARRIER_NAME" type="string" length="50"/>
      <many-to-one name="fromLocation" class="com.x.base.TransportPoint">
        <column name="FROM_POINT_ID"/>
      </many-to-one>
      <set name="details">
        <key column="INBOUND_ORDER_ID"/>
        <one-to-many class="com.x.oms.OmsInboundOrderDetail"/>
      </set>
    </joined-subclass>
  </class>
</hibernate-mapping>
"""


_JAVA_HQL = """
package com.x.service;
public class FileValidateServiceImpl {
    private static final String FIND_INBOUND_ORDER_HQL = "from OmsInboundOrder o where o.code = ?";
    public void check() {
        getSession().createQuery("select o from OmsInboundOrder o");
        getSession().createQuery("update OmsInboundOrder set status = 1 where id = ?");
    }
}
"""


def _tables(nodes):
    return {n.name: n for n in nodes if n.kind == NodeKind.DB_TABLE.value}


def _cols_of(nodes, table_lower):
    return {
        n.name.upper()
        for n in nodes
        if n.kind == NodeKind.DB_COLUMN.value and (n.meta or {}).get("table", "").lower() == table_lower
    }


def test_is_hbm_mapping_detects():
    assert is_hbm_mapping(_HBM)
    assert not is_hbm_mapping('<mapper namespace="x"><select id="a">select 1</select></mapper>')


def test_scan_hbm_emits_tables_with_entity_alias():
    nodes, _edges = _scan_hbm(_HBM, "oms/OmsOrder.hbm.xml", "p")
    tables = _tables(nodes)
    assert set(tables) == {"OMS_ORDER", "OMS_INBOUND_ORDER"}
    # 实体类名写进 meta (取 name= 末段) —— 桥的关键。
    assert tables["OMS_INBOUND_ORDER"].meta["entity_class"] == "OmsInboundOrder"
    assert tables["OMS_ORDER"].meta["entity_class"] == "OmsOrder"
    assert tables["OMS_INBOUND_ORDER"].meta["source"] == "hibernate-hbm"


def test_scan_hbm_inheritance_column_ownership():
    nodes, _edges = _scan_hbm(_HBM, "oms/OmsOrder.hbm.xml", "p")
    # 基类列只在基表 (不含子类的 CARRIER_NAME)。
    assert _cols_of(nodes, "oms_order") == {"ORDER_ID", "ORDER_NO"}
    # 子类列在子表: key + property + many-to-one 嵌套 column; <set> 集合列不算本表。
    assert _cols_of(nodes, "oms_inbound_order") == {"ORDER_ID", "CARRIER_NAME", "FROM_POINT_ID"}


def test_scan_hbm_malformed_fails_soft():
    nodes, edges = _scan_hbm("<hibernate-mapping><class table=", "x.hbm.xml", "p")
    assert nodes == [] and edges == []  # 解析失败返空, 不抛


def test_sql_plugin_detects_and_analyzes_hbm(tmp_path: Path):
    # 纯 Hibernate 仓 (只有 .hbm.xml, 无 .sql/注解) 也被 SqlPlugin 认到 + 产表节点。
    (tmp_path / "OmsOrder.hbm.xml").write_text(_HBM, encoding="utf-8")
    plugin = SqlPlugin()
    assert plugin.detect(tmp_path) is True
    result = plugin.analyze(tmp_path, "p")
    tables = {n.name for n in result.nodes if n.kind == NodeKind.DB_TABLE.value}
    assert {"OMS_ORDER", "OMS_INBOUND_ORDER"} <= tables


def test_sql_plugin_tolerates_non_utf8_file(tmp_path: Path):
    """遗留仓的非 UTF-8 (GBK) .sql 不该让整个 analyze 崩 —— 否则 1 个坏文件 = 全插件被
    ingest fail-soft 丢弃, 该仓 DB 层全黑 (2026-06-14 sample-project-beta 实证根因)。"""
    (tmp_path / "legacy.sql").write_bytes(b"CREATE TABLE t (\xbb\xff col1 INT);")  # 非 utf-8 字节
    (tmp_path / "OmsOrder.hbm.xml").write_text(_HBM, encoding="utf-8")
    result = SqlPlugin().analyze(tmp_path, "p")  # 不抛 UnicodeDecodeError
    tables = {n.name for n in result.nodes if n.kind == NodeKind.DB_TABLE.value}
    assert "OMS_INBOUND_ORDER" in tables  # 坏 .sql 不阻断后续 hbm 扫描


# ---- Hibernate HQL 访问边 (实体名 -> 表) ----


def test_scan_java_hql_resolves_entity_to_table():
    from codev_platform.plugins.builtin.sql.core import _scan_java_hql
    e2t = {"omsinboundorder": "oms_inbound_order"}
    nodes, edges = _scan_java_hql(
        _JAVA_HQL, "svc/FileValidateServiceImpl.java", "p", e2t, {"oms_inbound_order"}
    )
    funcs = [n for n in nodes if n.kind == NodeKind.BACKEND_FUNCTION.value]
    assert funcs and funcs[0].name == "FileValidateServiceImpl"  # 类粒度归属
    assert {e.target for e in edges} == {"p:db_table:oms_inbound_order"}
    kinds = {e.kind for e in edges}
    assert any("read" in k for k in kinds) and any("write" in k for k in kinds)  # select/from=读, update=写


def test_scan_java_hql_empty_entity_map_noop():
    from codev_platform.plugins.builtin.sql.core import _scan_java_hql
    nodes, edges = _scan_java_hql(_JAVA_HQL, "x.java", "p", {}, set())
    assert nodes == [] and edges == []  # 非 Hibernate 仓零开销


def test_scan_java_hql_ignores_non_entity_strings():
    from codev_platform.plugins.builtin.sql.core import _scan_java_hql
    src = 'class C { String s = "select item from Menu"; }'  # Menu 非已知实体
    nodes, edges = _scan_java_hql(src, "x.java", "p", {"omsinboundorder": "oms_inbound_order"}, set())
    assert nodes == [] and edges == []  # 杜绝非 HQL/未知实体串误连


def test_analyze_hbm_plus_hql_connects_table(tmp_path: Path):
    """端到端: hbm 定义 OMS_INBOUND_ORDER + java HQL 引用 OmsInboundOrder -> 表不再孤立。"""
    (tmp_path / "OmsOrder.hbm.xml").write_text(_HBM, encoding="utf-8")
    (tmp_path / "Svc.java").write_text(_JAVA_HQL, encoding="utf-8")
    result = SqlPlugin().analyze(tmp_path, "p")
    access = [
        e for e in result.edges
        if e.target == "p:db_table:oms_inbound_order" and "_table" in e.kind
    ]
    assert access  # HQL 访问边存在 -> find_table_usage 的 usage 不再空
