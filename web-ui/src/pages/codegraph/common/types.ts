// CodeGraph 共享类型别名 - 基于自动生成的 API 命名空间（src/services/apis/typings.d.ts）。
// 业务代码统一从这里 import，避免到处写 API.CodegraphNode 字面量。
// typings.d.ts 是 declare namespace API 全局声明，禁止 import，这里直接引用 API.*。

export type NodeDTO = API.CodegraphNode;
export type EdgeDTO = API.CodegraphEdge;
export type FileDTO = API.CodegraphFile;
export type SearchResponse = API.CodegraphSearchResponse;
export type NeighborsResponse = API.CodegraphNeighborsResponse;
export type FileTreeResponse = API.CodegraphFileTreeResponse;
export type GraphResponse = API.CodegraphGraphResponse;

// 请求入参
export type SearchRequest = API.CodegraphSearchRequest;
export type NeighborsRequest = API.CodegraphNeighborsRequest;
export type GraphRequest = API.CodegraphGraphRequest;

// 跨层链路 cross-link 类型别名（仅 crosslink 子页用到的）
export type CrossLinkTablesResponse = API.CrossLinkTablesResponse;
export type CrossLinkGraphResponse = API.CrossLinkGraphResponse;
export type CrossLinkGraphRequest = API.CrossLinkGraphRequest;

// 节点 kind / 边 kind / 语言 — 自动生成 typings 把它们标成 any，这里收紧
// 业务列表（用于 Select options 和颜色映射 fallback）
export type NodeKind =
  | 'file'
  | 'class'
  | 'interface'
  | 'method'
  | 'function'
  | 'field'
  | 'variable'
  | 'import'
  | 'enum'
  | 'enum_member'
  | 'route'
  | 'constant'
  | 'type_alias'
  | 'frontend_page'
  | 'frontend_api'
  | 'backend_endpoint'
  | 'java_method'
  | 'python_method'
  | 'flyway_migration'
  | 'table'
  | 'column';

export type EdgeKind =
  | 'contains'
  | 'calls'
  | 'imports'
  | 'instantiates'
  | 'references'
  | 'extends'
  | 'implements'
  | 'page_calls_api'
  | 'calls_api'
  | 'controller_calls_facade'
  | 'facade_calls_service'
  | 'service_calls_mapper'
  | 'queries_table'
  | 'writes_table'
  | 'reads_table'
  | 'updates_table'
  | 'defines_table'
  | 'defines_column'
  | 'calls_method';

export type Language = 'java' | 'python' | 'tsx' | 'typescript' | 'javascript';
