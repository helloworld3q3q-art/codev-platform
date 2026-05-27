"""codev_platform.cross_link — 跨层 KG sqlite 引擎 (schema + MCP server + query helpers).

模块组织:
    schema   节点/边表定义 + open_db (per-project, legacy fallback)
    server   MCP server 暴露 find_endpoint_link / find_table_refs / search_nodes
    query    Python API: full-stack chain 查询 (业务侧调用)

scanner (scan_flyway / scan_java_mappers / scan_python_repos / ...) 仍在各业务仓
tools/cross_link/, 因为它们扫的是业务代码结构, 不通用。
"""
