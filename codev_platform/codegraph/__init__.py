"""codegraph 多租户代理: 把外部 `codegraph serve --mcp` (per-repo stdio) 包成平台统一的
多租户 SSE MCP server (?project_id= 路由), 与 chroma / graph 对齐。"""
