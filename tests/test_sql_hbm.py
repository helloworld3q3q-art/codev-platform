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
    ingest fail-soft 丢弃, 该仓 DB 层全黑 (2026-06-14 ideas-v2 实证根因)。"""
    (tmp_path / "legacy.sql").write_bytes(b"CREATE TABLE t (\xbb\xff col1 INT);")  # 非 utf-8 字节
    (tmp_path / "OmsOrder.hbm.xml").write_text(_HBM, encoding="utf-8")
    result = SqlPlugin().analyze(tmp_path, "p")  # 不抛 UnicodeDecodeError
    tables = {n.name for n in result.nodes if n.kind == NodeKind.DB_TABLE.value}
    assert "OMS_INBOUND_ORDER" in tables  # 坏 .sql 不阻断后续 hbm 扫描
