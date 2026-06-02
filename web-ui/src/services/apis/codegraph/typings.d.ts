declare namespace CG {
      
  // any 类型定义
type any = any;

// CommonResultCrossLinkTablesResponse 响应数据
interface CommonResultCrossLinkTablesResponse {
  result?: number;
  message?: string;
  data?: CrossLinkTablesResponse;
  ok?: boolean;
}

// Cross-layer KG 全表名清单响应
interface CrossLinkTablesResponse {
  tables?: string[]; // 业务表名清单（按字母排序）
}

// Cross-layer 按表名查引用请求
interface CrossLinkTableRefsRequest {
  table: string; // 业务表名（不带 schema，如 stock_recommend_result）
}

// CommonResultCrossLinkTableRefsResponse 响应数据
interface CommonResultCrossLinkTableRefsResponse {
  result?: number;
  message?: string;
  data?: CrossLinkTableRefsResponse;
  ok?: boolean;
}

// Cross-layer KG 引用项
interface CrossLinkNodeRef {
  name?: string; // 节点名（方法全名 / Flyway 文件名）
  kind?: string; // 节点 kind
  path?: string; // 源文件相对路径
  line?: number; // 源文件行号
  confidence?: number; // 边置信度（1.0 = 精确锚点，0.7 = fuzzy）
  evidence?: string; // 证据片段（SQL 行或调用点摘要）
}

// Cross-layer 按表名查引用响应
interface CrossLinkTableRefsResponse {
  table?: string; // 查询的表名
  definers?: CrossLinkNodeRef[]; // 建表 / 变更表的 Flyway migration 列表
  javaReaders?: CrossLinkNodeRef[]; // SELECT/JOIN 该表的 Java 方法列表
  javaWriters?: CrossLinkNodeRef[]; // INSERT 该表的 Java 方法列表
  javaUpdaters?: CrossLinkNodeRef[]; // UPDATE/DELETE 该表的 Java 方法列表
  pythonReaders?: CrossLinkNodeRef[]; // SELECT 该表的 Python 方法列表
  pythonWriters?: CrossLinkNodeRef[]; // INSERT 该表的 Python 方法列表
  pythonUpdaters?: CrossLinkNodeRef[]; // UPDATE/DELETE 该表的 Python 方法列表
}

// CommonResultCrossLinkStatsResponse 响应数据
interface CommonResultCrossLinkStatsResponse {
  result?: number;
  message?: string;
  data?: CrossLinkStatsResponse;
  ok?: boolean;
}

// Cross-layer KG 概览统计响应
interface CrossLinkStatsResponse {
  lastBuildAt?: string; // 最近一次 build_index 时间（来自 build_meta.last_build_at）
  nodesByKind?: Record<string, number>; // 节点按 kind 计数（table / column / java_method 等）
  edgesByRel?: Record<string, number>; // 边按 rel 计数（defines_column / queries_table 等）
}

// Cross-layer 节点模糊检索请求
interface CrossLinkSearchNodesRequest {
  query: string; // 搜索关键字（LIKE %query% COLLATE NOCASE）
  kind?: string; // 节点 kind 过滤，'all' 不过滤；支持 table / column / java_method / java_endpoint / python_method / frontend_api / flyway_migration
  limit?: number; // 返回上限（默认 20，最大 50）
}

// CommonResultCrossLinkSearchNodesResponse 响应数据
interface CommonResultCrossLinkSearchNodesResponse {
  result?: number;
  message?: string;
  data?: CrossLinkSearchNodesResponse;
  ok?: boolean;
}

// Cross-layer 节点检索命中项
interface CrossLinkSearchHit {
  name?: string; // 节点名
  kind?: string; // 节点 kind
  path?: string; // 源文件相对路径（部分节点为 NULL，如全局 table）
  line?: number; // 源文件行号
  language?: string; // 语言（java / python / sql / typescript 等）
  meta?: Record<string, Record<string, any>>; // meta_json 解析后的扩展元数据（如 url / signature 等）
}

// Cross-layer 节点模糊检索响应
interface CrossLinkSearchNodesResponse {
  query?: string; // 回显查询关键字
  kind?: string; // 回显 kind 过滤
  hits?: CrossLinkSearchHit[]; // 命中节点列表（按 kind, name 排序）
}

// Cross-link 图谱查询入参
interface CrossLinkGraphRequest {
  mode?: string; // 图谱模式：overview=跨层总览，full=完整图，kind=指定节点类型。默认 overview。
  kinds?: string[]; // 包含的节点类型。kind 模式必传；full/overview 模式可作为进一步过滤。
  excludeKinds?: string[]; // 排除的节点类型。
  rels?: string[]; // 包含的关系类型。
  excludeRels?: string[]; // 排除的关系类型。
  limit?: number; // 返回节点上限。
}

// CommonResultCrossLinkGraphResponse 响应数据
interface CommonResultCrossLinkGraphResponse {
  result?: number;
  message?: string;
  data?: CrossLinkGraphResponse;
  ok?: boolean;
}

// Cross-link 边
interface CrossLinkGraphEdge {
  source?: string; // 源节点 id
  target?: string; // 目标节点 id
  kind?: string; // 关系类型
}

// Cross-link 节点
interface CrossLinkGraphNode {
  id?: string; // 节点 id（kind:name 复合键）
  kind?: string; // 节点 kind
  name?: string; // 节点名
  filePath?: string; // 源文件路径（如有）
  startLine?: number; // 源文件行号（如有）
  language?: string; // 语言
}

// Cross-link 全图响应
interface CrossLinkGraphResponse {
  nodes?: CrossLinkGraphNode[]; // 全部节点（按 kind 排序）
  edges?: CrossLinkGraphEdge[]; // 全部边（kind 含 defines_table / defines_column / queries_table / writes_table / updates_table / reads_table / calls_api）
  nodeCount?: number; // 节点数
  edgeCount?: number; // 边数
}

// Cross-layer endpoint 双向关联请求
interface CrossLinkEndpointLinkRequest {
  name: string; // frontend_api 函数名（如 'postStocksPage'）或 java_endpoint 全名（如 'StockController.page'）
}

// CommonResultListCrossLinkEndpointLinkItem 接口
interface CommonResultListCrossLinkEndpointLinkItem {
  result?: number;
  message?: string;
  data?: CrossLinkEndpointLinkItem[];
  ok?: boolean;
}

// Cross-layer endpoint 关联响应单项
interface CrossLinkEndpointLinkItem {
  node?: string; // 源节点名（即入参 name）
  kind?: string; // 源节点 kind（frontend_api / java_endpoint）
  path?: string; // 源节点文件路径
  line?: number; // 源节点行号
  url?: string; // 源节点 URL（来自 meta_json.url）
  direction?: string; // 关联方向（frontend -> java 或 java <- frontend）
  targets?: CrossLinkEndpointTarget[]; // 下游 Java endpoint 列表（仅 frontend → java 方向有值）
  callers?: CrossLinkEndpointTarget[]; // 上游前端 API 列表（仅 java ← frontend 方向有值）
}

// Cross-layer endpoint 关联项
interface CrossLinkEndpointTarget {
  name?: string; // 节点名（frontend_api 函数名 或 java_endpoint 全名）
  path?: string; // 源文件相对路径
  line?: number; // 源文件行号
  url?: string; // HTTP URL（来自 meta_json.url）
  confidence?: number; // 边置信度（1.0 = 精确锚点）
  evidence?: string; // 证据片段
}

// CommonResultStatsResponse 响应数据
interface CommonResultStatsResponse {
  result?: number;
  message?: string;
  data?: StatsResponse;
  ok?: boolean;
}

// codegraph 索引总览：文件数 / 节点数 / 边数 + 多维分布
interface StatsResponse {
  totalFiles?: number; // 已索引文件总数
  totalNodes?: number; // 节点总数
  totalEdges?: number; // 关系边总数
  byLanguage?: Record<string, number>; // 按语言分布的节点数：language -> count
  byNodeKind?: Record<string, number>; // 按 kind 分布的节点数：node kind -> count
  byEdgeKind?: Record<string, number>; // 按 kind 分布的边数：edge kind -> count
}

// 节点全文检索请求
interface SearchRequest {
  keyword: string; // 搜索关键字（最少 1 字符；自动按 prefix 匹配，特殊字符转义）
  languages?: string[]; // 语言过滤（可选）：java / python / tsx / typescript / javascript
  kinds?: string[]; // 节点 kind 过滤（可选）
  limit?: number; // 返回上限，默认 50；最大 500
}

// CommonResultSearchResponse 响应数据
interface CommonResultSearchResponse {
  result?: number;
  message?: string;
  data?: SearchResponse;
  ok?: boolean;
}

// 代码节点（class / function / file 等）
interface NodeDTO {
  id?: string; // 节点 id（形如 class:hash / file:path）
  kind?: string; // 节点类型：file / class / function / method / interface / import / route 等
  name?: string; // 符号名
  qualifiedName?: string; // 限定名 (含包路径 / 模块前缀)
  filePath?: string; // 所在文件路径（项目相对路径）
  language?: string; // 编程语言：java / python / tsx / typescript / javascript
  startLine?: number; // 起始行号（1 起）
  endLine?: number; // 结束行号
  startColumn?: number; // 起始列号
  endColumn?: number; // 结束列号
  docstring?: string; // 文档注释（Javadoc / docstring 等）
  signature?: string; // 签名（含参数 / 返回类型）
  visibility?: string; // 可见性：public / private / protected / package
  isExported?: boolean; // 是否被导出 / 公开（语言相关）
  isAsync?: boolean; // 是否 async 函数
  isStatic?: boolean; // 是否 static
  isAbstract?: boolean; // 是否 abstract
}

// 节点检索响应
interface SearchResponse {
  items?: NodeDTO[]; // 命中的节点列表（按 FTS5 相关度排序）
}

// 按 id 查询节点详情
interface NodeRequest {
  id: string; // 节点 id
}

// CommonResultNodeDTO 数据传输对象
interface CommonResultNodeDTO {
  result?: number;
  message?: string;
  data?: NodeDTO;
  ok?: boolean;
}

// 取节点邻居（depth=1）
interface NeighborsRequest {
  id: string; // 中心节点 id
  direction?: string; // 方向：in / out / both，默认 both
  edgeKinds?: string[]; // 边 kind 过滤（可选）
  depth?: number; // 深度（当前仅支持 1）
}

// CommonResultNeighborsResponse 响应数据
interface CommonResultNeighborsResponse {
  result?: number;
  message?: string;
  data?: NeighborsResponse;
  ok?: boolean;
}

// 节点关系边（contains / calls / imports / extends 等）
interface EdgeDTO {
  id?: number; // 边自增 id
  source?: string; // 源节点 id
  target?: string; // 目标节点 id
  kind?: string; // 关系类型：contains / calls / imports / instantiates / references / extends / implements
  line?: number; // 调用 / 引用所在行号（可空）
  col?: number; // 调用 / 引用所在列号（可空）
}

// 中心节点 + 邻居 + 相关边的子图
interface NeighborsResponse {
  center?: NodeDTO;
  nodes?: NodeDTO[]; // 邻居节点（不含中心）
  edges?: EdgeDTO[]; // 中心与邻居之间的边
}

// GraphRequest 请求参数
interface GraphRequest {
  limit?: number; // 节点数量上限（防止把浏览器卡死），默认 2000，最大 20000
  languages?: string[]; // 按语言过滤（java / python / tsx / typescript / javascript），空表示全部
  kinds?: string[]; // 按节点 kind 过滤，空表示全部
  edgeKinds?: string[]; // 按边 kind 过滤（contains / calls / imports / ...），空表示全部
}

// CommonResultGraphResponse 响应数据
interface CommonResultGraphResponse {
  result?: number;
  message?: string;
  data?: GraphResponse;
  ok?: boolean;
}

// GraphResponse 响应数据
interface GraphResponse {
  nodes?: NodeDTO[]; // 所有节点（可能被 limit 截断）
  edges?: EdgeDTO[]; // 所有边（仅保留两端都在 nodes 列表里的）
  totalNodes?: number; // DB 实际节点总数（不受 limit 影响）
  totalEdges?: number; // DB 实际边总数
}

// 查询已索引文件（可按 path 前缀过滤）
interface FileTreeRequest {
  prefix?: string; // path 前缀过滤（可空），例如 apps/stock-admin-api/
}

// CommonResultFileTreeResponse 响应数据
interface CommonResultFileTreeResponse {
  result?: number;
  message?: string;
  data?: FileTreeResponse;
  ok?: boolean;
}

// 已索引文件
interface FileDTO {
  path?: string; // 项目相对路径
  language?: string; // 语言
  size?: number; // 文件字节数
  nodeCount?: number; // 文件中索引到的节点数
}

// 已索引文件列表（按 path 升序）
interface FileTreeResponse {
  items?: FileDTO[]; // 文件列表
};
    }