"""builtin.sql Pass 4d — MyBatis XML Mapper 表访问血缘扫描测试。

覆盖: 基本 select/insert/update/delete → reads/writes_table、动态标签 <if> 里的 JOIN 表也抽到、
CDATA 解包、namespace 归属 + language=java、非 Mapper .xml 跳过。
"""
from __future__ import annotations

from codev_platform.graph.schema import EdgeKind, NodeKind
from codev_platform.plugins.builtin.sql.xml_mapper import (
    _scan_xml_mapper,
    _strip_dynamic,
    is_mybatis_mapper,
)

_XML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN" "http://mybatis.org/dtd">
<mapper namespace="com.x.OrderMapper">
  <select id="findById" resultType="Order">
    SELECT * FROM orders WHERE id = #{id}
  </select>
  <select id="listWithItems" resultType="Order">
    SELECT o.* FROM orders o
    <if test="withItems"> JOIN order_items i ON i.order_id = o.id </if>
    WHERE o.status = #{status}
  </select>
  <insert id="create">INSERT INTO orders (id, name) VALUES (#{id}, #{name})</insert>
  <update id="touch">UPDATE orders SET updated = now() WHERE id = #{id}</update>
  <delete id="purge">DELETE FROM <![CDATA[ stock_alert_event ]]> WHERE id = #{id}</delete>
</mapper>
"""


def _scan(src=_XML, known=None):
    return _scan_xml_mapper(src, "mapper/OrderMapper.xml", "p", known or set())


def _tables(edges, kind):
    return {e.target.rsplit(":", 1)[-1] for e in edges if e.kind == kind}


def test_is_mybatis_mapper_detects_mapper_not_spring():
    assert is_mybatis_mapper(_XML)
    assert not is_mybatis_mapper('<beans><bean id="x"/></beans>')  # Spring beans, 非 mapper


def test_strip_dynamic_removes_tags_cdata_placeholders():
    s = _strip_dynamic('SELECT * FROM t <if test="x">JOIN u</if> WHERE a = #{a}')
    assert "JOIN u" in s and "<if" not in s and "#{a}" not in s and "?" in s
    assert "stock" in _strip_dynamic("DELETE FROM <![CDATA[ stock ]]>")  # CDATA 解包


def test_select_reads_table_including_dynamic_join():
    _, edges = _scan()
    reads = _tables(edges, EdgeKind.READS_TABLE.value)
    assert "orders" in reads
    assert "order_items" in reads  # 动态 <if> 标签里的 JOIN 表也抽到 (去标签不丢表名)


def test_insert_update_delete_write_table():
    _, edges = _scan()
    writes = _tables(edges, EdgeKind.WRITES_TABLE.value)
    assert "orders" in writes               # insert/update orders
    assert "stock_alert_event" in writes    # delete + CDATA 表


def test_func_node_namespace_java_lang_and_meta():
    nodes, _ = _scan()
    funcs = [n for n in nodes if n.kind == NodeKind.BACKEND_FUNCTION.value]
    assert funcs and all(n.language == "java" for n in funcs)
    assert all(n.meta.get("mybatis_xml") for n in funcs)
    assert any(n.id.endswith("com.x.OrderMapper.findById") for n in funcs)


def test_non_mapper_xml_yields_nothing():
    nodes, edges = _scan_xml_mapper("<beans/>", "pom.xml", "p", set())
    assert not nodes and not edges
