// 统一图谱共享类型 — 基于自动生成的 API 命名空间 (src/services/apis/typings.d.ts)。
// typings.d.ts 是 declare namespace API 全局声明, 禁止 import, 这里直接引用 API.*。
// 注意: 这些类型在执行 `pnpm run api` (后端新增 unified 接口后) 才会生成。

export type UnifiedGraphNode = API.UnifiedGraphNode;
export type UnifiedGraphEdge = API.UnifiedGraphEdge;
export type UnifiedGraphResponse = API.UnifiedGraphResponse;
export type UnifiedGraphStatsResponse = API.UnifiedGraphStatsResponse;

// 统一节点 kind (对齐后端 graph/schema.py:NodeKind 开放枚举)。
export type UnifiedNodeKind =
  | 'project'
  | 'file'
  | 'frontend_route'
  | 'frontend_component'
  | 'frontend_api_call'
  | 'backend_endpoint'
  | 'backend_function'
  | 'db_table'
  | 'db_column'
  | 'wiki_page'
  | 'jira_issue'
  | 'feishu_doc'
  | 'git_commit'
  | 'pull_request';
