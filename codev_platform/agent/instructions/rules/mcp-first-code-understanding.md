# MCP-first 代码理解规则

本规则只给 Web agent 运行时使用,不参与 Codex/Claude 工作区的 `sync-rules` 分发。

## 目标

回答代码、架构、规则问题时,先用绑定到当前项目的工具拿证据,再给结论。

## 工具顺序

1. 涉及跨层影响、数据流、接口调用方、表使用、页面依赖时,先用图谱工具:
   `impact_analysis`、`table_usage`、`api_callers`、`page_dependencies`。
2. 涉及规则、设计文档、操作手册、历史决策时,先用 `search_docs`。
3. 涉及代码符号、定义位置、调用链时,先用 `codegraph_search`,再接 `codegraph_callers` 或 `codegraph_callees`。
4. 只有前置工具已经定位到具体文件后,才用 `read_file`。
5. 不清楚目录结构或路径不确定时,才用 `list_dir`。

## 护栏

- 不编造文件路径、符号、表、接口、工具结果。
- `impact_analysis(nodeRef)` 需要真实图谱节点引用。不要把用户自然语言里的接口字符串直接塞进去,除非图谱工具已返回该节点。
- 概览问题最多使用 3 个工具。
- 普通修改面分析最多使用 8 个工具,除非用户明确要求完整审计。
- 索引信息不足时,说明没找到什么、已有证据是什么。
- 先回答最小闭环结论,再补证据,不要默认读完整仓。
