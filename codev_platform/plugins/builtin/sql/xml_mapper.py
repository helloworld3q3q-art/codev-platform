"""builtin.sql 的 MyBatis XML Mapper 扫描域 (Pass 4d)。

补 Java 表访问血缘的第三种写法:XML Mapper(`*.xml` 里 `<select>/<insert>/<update>/<delete>`
标签的 SQL)。注解 SQL(core._scan_java_dml, Pass 4b) + MyBatis-Plus(Pass 4c) 之外, 很多 Java
仓把复杂 SQL 写在 XML 里 —— 这层不扫则该仓"表 ↔ Java 读写"边整段缺失(且 detect 静默不报)。

对称 core._scan_java_dml:取 SQL 文本 → _sql_table_access 抽读写表 → _emit_table_access 产边。
SQL 文本来自 XML 标签体(去 MyBatis 动态标签 <if>/<where>/<foreach> + 占位符 #{}/${}):表
血缘只看 FROM/JOIN/INSERT/UPDATE/DELETE 后的表名, 动态条件不含表名, 故去标签不损表准确度。
"""
from __future__ import annotations

import re

from codev_platform.graph.schema import GraphEdge, GraphNode, NodeKind
from codev_platform.plugins.builtin.sql._common import _emit_table_access
from codev_platform.plugins.builtin.sql.core import _sql_table_access

# <select|insert|update|delete ... id="xxx" ...>body</same-tag> (id 属性任意位置, body 跨行)。
_RE_STMT = re.compile(
    r'<(select|insert|update|delete)\b[^>]*\bid\s*=\s*"([^"]+)"[^>]*>(.*?)</\1>',
    re.IGNORECASE | re.DOTALL,
)
# <mapper namespace="com.x.UserMapper"> —— backend_function 归属命名空间。
_RE_NS = re.compile(r'<mapper\b[^>]*\bnamespace\s*=\s*"([^"]+)"', re.IGNORECASE)
# 仅含 <mapper 根标签的 .xml 才是 MyBatis Mapper (排除 Spring beans / pom / 其它 XML)。
_RE_IS_MAPPER = re.compile(r"<mapper\b", re.IGNORECASE)


def is_mybatis_mapper(text: str) -> bool:
    """廉价判定:.xml 是否 MyBatis Mapper (含 <mapper 根标签)。detect / analyze 共用。"""
    return bool(_RE_IS_MAPPER.search(text))


def _strip_dynamic(sql: str) -> str:
    """MyBatis 动态 SQL → 可解析 SQL:解 CDATA、去 XML 注释、去 <if>/<where>/<foreach> 等标签、占位符 → ?。"""
    sql = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", sql, flags=re.DOTALL)  # 解 CDATA 包装, 保留内容
    sql = re.sub(r"<!--.*?-->", " ", sql, flags=re.DOTALL)              # 去 XML 注释
    sql = re.sub(r"<[^>]+>", " ", sql)                                  # 去动态标签 (<if>/<where>/<foreach>/<include>)
    sql = re.sub(r"[#$]\{[^}]*\}", "?", sql)                            # #{x}/${x} 占位 → ?
    return sql


def _scan_xml_mapper(
    src: str, rel: str, project_id: str, known_tables: set[str]
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """扫一个 MyBatis XML Mapper -> backend_function(language=java) 节点 + reads/writes_table 边。

    func_id = "<pid>:backend_function:<rel>:<namespace>.<stmt-id>" (与注解 SQL 同 backend_function
    kind, 跨写法产同表的 reads/writes_table 边可去重 + 链接)。未定义表走 _emit_table_access 的
    inferred stub (血缘不断)。confidence 沿用默认 1.0 (去标签只丢 WHERE 条件, 表名抽取与注解 SQL 同款)。
    """
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    seen_func: set[str] = set()
    seen_stub: set[str] = set()
    ns_m = _RE_NS.search(src)
    ns = ns_m.group(1) if ns_m else rel
    for _kw, sid, body in _RE_STMT.findall(src):
        writes, reads = _sql_table_access(_strip_dynamic(body))
        if not writes and not reads:
            continue
        func_id = f"{project_id}:backend_function:{rel}:{ns}.{sid}"
        if func_id not in seen_func:
            seen_func.add(func_id)
            nodes.append(
                GraphNode(
                    id=func_id,
                    kind=NodeKind.BACKEND_FUNCTION.value,
                    name=f"{ns.rsplit('.', 1)[-1]}.{sid}",
                    project_id=project_id,
                    file=rel,
                    language="java",
                    meta={"db_access": True, "mybatis_xml": True, "namespace": ns},
                )
            )
        a_nodes, a_edges = _emit_table_access(
            func_id, writes, reads, project_id, known_tables, seen_stub
        )
        nodes.extend(a_nodes)
        edges.extend(a_edges)
    return nodes, edges
