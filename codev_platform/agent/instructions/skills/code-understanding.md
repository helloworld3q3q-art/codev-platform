# Skill: code-understanding

本 skill 只给 Web agent 运行时使用,不假设 shell 权限、Claude Code hook 或 AskUserQuestion。

## 触发场景

- 用户问"这个项目有什么用"。
- 用户问某个功能在哪里实现。
- 用户问一个需求要改哪些后端/前端文件。
- 用户问接口、数据库表、页面、组件、函数之间的关系。
- 用户要求解释代码或架构。

## 执行流程

1. 先判断问题类型:项目概览、修改面、影响面、符号定位、规则查询。
2. 按类型选择第一个工具:
   - 找相关代码 / 符号定位:**优先 `code_recall`**(一次融合 graph + codegraph 给最相关代码实体, 带来源),再按需 `read_file` 读实现 / `codegraph_callers` 看调用方。比分别调 codegraph 搜 + 查图谱再合并更省。
   - 项目概览:先 `code_recall` 或 `search_docs` 拿相关代码/文档;已知具体文件再 `read_file`。
   - 影响面:`code_recall` 定位起点节点后,用 `impact_analysis` / `table_usage` 算跨层链路。
   - 规则查询:优先 `search_docs`。
3. 工具数量要和问题规模匹配:
   - 概览:1-3 个工具。
   - 普通修改面:最多 8 个工具。
   - 完整审计:只有用户明确要求时才展开。
4. 回答要简洁,并带文件、接口、表或工具结果证据。
5. 工具找不到时,报告限制,不要猜路径或符号。
