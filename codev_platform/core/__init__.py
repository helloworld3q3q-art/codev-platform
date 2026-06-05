"""Internal shared infrastructure for multi-project AI tooling.

模块归属: tools/_platform/
设计目标: 本地原型 + 未来 server 部署共用同一份解析 / 路径约定逻辑。

子模块:
- project_id: project_id 解析 (env / .claude/project.json / HTTP header) + 格式校验
- paths: 索引产物路径约定 (chroma collection 前缀 / codegraph / graph)
"""
