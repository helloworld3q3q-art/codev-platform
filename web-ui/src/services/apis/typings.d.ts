declare namespace API {
      
  // any 类型定义
type any = any;

// ApiCallersRequest 请求参数
interface ApiCallersRequest {
  endpointRef: string;
}

// 审计记录对外视图 (access.jsonl 单条)。
interface AuditItem {
  ts?: any; // 时间 ISO8601
  service?: any; // 服务名
  userId?: any; // 用户 ID
  orgId?: any; // 组织 ID
  via?: any; // 鉴权方式 (token / passthrough)
  projectId?: any; // 项目 ID
  allowed?: boolean; // 是否放行
  reason?: any; // 判定原因
}

// POST /api/v1/audit/list 请求体 —— 过滤条件 (全可选) + 分页 (继承 PageBody)。
interface AuditListRequest {
  pageNumber?: number; // 页码, 从 1 起
  pageSize?: number; // 每页数量 (上限 200)
  service?: any; // 服务名: codev-web / codev-agent
  userId?: any; // 用户 ID
  orgId?: any; // 组织 ID (仅 platform_admin 可指定; org_admin 忽略)
  projectId?: any; // 项目 ID
  allowed?: any; // 授权结果: true 放行 / false 拒绝
  tsFrom?: any; // 起始时间 ISO8601 (含界)
  tsTo?: any; // 结束时间 ISO8601 (含界)
}

// 对话结果 (agent ChatResponse 的 web 投影)。
interface ChatData {
  sessionId: string; // 会话 id (回填, 用于下轮)
  answer: string; // 最终回答
  steps?: ChatStep[]; // 推理 / 工具调用轨迹
  usage?: Record<string, any>; // token / 调用统计
  stopReason: string; // 收尾原因 (final / max_steps / ...)
}

// POST /api/v1/agent/chat 请求体。
interface ChatRequest {
  question: string; // 问题
  sessionId?: any; // 多轮会话 id; 省略=新会话
  maxSteps?: any; // 本次循环 step 上限; 省略走 agent config
}

// 单步轨迹 (对齐 agent.schemas.StepOut)。
interface ChatStep {
  n: number;
  thought?: any;
  tool?: any;
  args?: any;
  resultSummary?: any;
}

// 对齐 Java EdgeDTO。
interface CodegraphEdge {
  id?: any;
  source?: any;
  target?: any;
  kind?: any;
  line?: any;
  col?: any;
}

// 对齐 Java FileDTO。
interface CodegraphFile {
  path?: any;
  language?: any;
  size?: any;
  nodeCount?: any;
}

// CodegraphFileTreeRequest 请求参数
interface CodegraphFileTreeRequest {
  prefix?: any;
}

// CodegraphFileTreeResponse 响应数据
interface CodegraphFileTreeResponse {
  items?: CodegraphFile[];
}

// CodegraphGraphRequest 请求参数
interface CodegraphGraphRequest {
  limit?: any;
  languages?: any;
  kinds?: any;
  edgeKinds?: any;
}

// CodegraphGraphResponse 响应数据
interface CodegraphGraphResponse {
  nodes?: CodegraphNode[];
  edges?: CodegraphEdge[];
  totalNodes?: number;
  totalEdges?: number;
}

// CodegraphNeighborsRequest 请求参数
interface CodegraphNeighborsRequest {
  id?: any;
  direction?: any;
  edgeKinds?: any;
  depth?: any;
}

// CodegraphNeighborsResponse 响应数据
interface CodegraphNeighborsResponse {
  center?: any;
  nodes?: CodegraphNode[];
  edges?: CodegraphEdge[];
}

// 对齐 Java NodeDTO。
interface CodegraphNode {
  id?: any;
  kind?: any;
  name?: any;
  qualifiedName?: any;
  filePath?: any;
  language?: any;
  startLine?: any;
  endLine?: any;
  startColumn?: any;
  endColumn?: any;
  docstring?: any;
  signature?: any;
  visibility?: any;
  isExported?: any;
  isAsync?: any;
  isStatic?: any;
  isAbstract?: any;
}

// CodegraphNodeRequest 请求参数
interface CodegraphNodeRequest {
  id?: any;
}

// CodegraphSearchRequest 请求参数
interface CodegraphSearchRequest {
  keyword?: any;
  languages?: any;
  kinds?: any;
  limit?: any;
}

// CodegraphSearchResponse 响应数据
interface CodegraphSearchResponse {
  items?: CodegraphNode[];
}

// 对齐 Java StatsResponse。
interface CodegraphStatsResponse {
  totalFiles?: number;
  totalNodes?: number;
  totalEdges?: number;
  byLanguage?: Record<string, number>;
  byNodeKind?: Record<string, number>;
  byEdgeKind?: Record<string, number>;
}

// CommonResult_ChatData_ 接口
interface CommonResult_ChatData_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_CodegraphFileTreeResponse_ 响应数据
interface CommonResult_CodegraphFileTreeResponse_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_CodegraphGraphResponse_ 响应数据
interface CommonResult_CodegraphGraphResponse_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_CodegraphNeighborsResponse_ 响应数据
interface CommonResult_CodegraphNeighborsResponse_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_CodegraphNode_ 接口
interface CommonResult_CodegraphNode_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_CodegraphSearchResponse_ 响应数据
interface CommonResult_CodegraphSearchResponse_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_CodegraphStatsResponse_ 响应数据
interface CommonResult_CodegraphStatsResponse_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_GraphQueryResponse_ 响应数据
interface CommonResult_GraphQueryResponse_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_HealthData_ 接口
interface CommonResult_HealthData_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_ImpactReportResponse_ 响应数据
interface CommonResult_ImpactReportResponse_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_IndexStatusResponse_ 响应数据
interface CommonResult_IndexStatusResponse_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_JobDTO_ 数据传输对象
interface CommonResult_JobDTO_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_JobIdData_ 接口
interface CommonResult_JobIdData_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_McpUsageReportResponse_ 响应数据
interface CommonResult_McpUsageReportResponse_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_MemberActionResult_ 接口
interface CommonResult_MemberActionResult_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_MemoryItem_ 接口
interface CommonResult_MemoryItem_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_NoneType_ 接口
interface CommonResult_NoneType_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_OrgActionResult_ 接口
interface CommonResult_OrgActionResult_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_OrgItem_ 接口
interface CommonResult_OrgItem_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_ProjectActionResult_ 接口
interface CommonResult_ProjectActionResult_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_ProjectListItem_ 接口
interface CommonResult_ProjectListItem_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_PublicKeyInfo_ 接口
interface CommonResult_PublicKeyInfo_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_SessionInfo_ 接口
interface CommonResult_SessionInfo_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_TokenPair_ 接口
interface CommonResult_TokenPair_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_UnifiedGraphResponse_ 响应数据
interface CommonResult_UnifiedGraphResponse_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_UnifiedGraphStatsResponse_ 响应数据
interface CommonResult_UnifiedGraphStatsResponse_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_UserActionResult_ 接口
interface CommonResult_UserActionResult_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_UserItem_ 接口
interface CommonResult_UserItem_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_dict_str__list_EnumItem___ 接口
interface CommonResult_dict_str__list_EnumItem___ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_list_MemoryItem__ 接口
interface CommonResult_list_MemoryItem__ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_list_OrgSelectionItem__ 接口
interface CommonResult_list_OrgSelectionItem__ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_list_SessionItem__ 接口
interface CommonResult_list_SessionItem__ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_list_SessionMessageItem__ 接口
interface CommonResult_list_SessionMessageItem__ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_list_UserSelectionItem__ 接口
interface CommonResult_list_UserSelectionItem__ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// 枚举项响应 DTO。字段名 camelCase 对齐前端 (Java EnumItemDTO 同构)。
interface EnumItem {
  enumType: string;
  enumValue: string;
  localLanguage: string;
  enumOrder: number;
  displayName: string;
  description?: string;
}

// EnumListRequest 请求参数
interface EnumListRequest {
  enumType?: any; // 枚举类型; 不传返回全部
}

// 错误项 (对齐前端 BaseApiResponse.errors[])。
interface ErrorItem {
  errorCode: string;
  errorMessage: string;
  field?: any;
}

// table-usage / page-deps / api-callers 通用容器 (结构随查询不同, data 透传)。
interface GraphQueryResponse {
  found?: boolean;
  data?: any;
}

// HTTPValidationError 接口
interface HTTPValidationError {
  detail?: ValidationError[];
}

// HealthData 接口
interface HealthData {
  status?: string;
  service?: string;
  dependencies?: Record<string, string>;
}

// ImpactReportResponse 响应数据
interface ImpactReportResponse {
  found?: boolean;
  target?: any;
  impact?: any;
  risk?: any;
  layersAffected?: string[];
  total?: number;
  summary?: string;
  ambiguous?: Record<string, any>[];
}

// ImpactRequest 请求参数
interface ImpactRequest {
  nodeRef: string;
}

// POST /api/v1/indexes/rebuild 请求体。
interface IndexRebuildRequest {
  indexKind?: any; // 索引类型: all / chroma / codegraph; 不传默认 all
}

// 单类索引的新鲜度 (来自统一 IndexManifest, Phase 1)。
interface IndexStatusItem {
  kind?: any; // 索引类型 chroma/codegraph/graph/docs
  status?: any; // 上次构建状态 ok/failed
  gitCommit?: any; // 构建时仓库 HEAD commit
  fresh?: any; // 是否对齐当前 HEAD: true 对齐/false 落后/null 未知
  reason?: any; // 新鲜度判定说明
  finishedAt?: any; // 上次构建完成时间 epoch 秒
  elapsedSec?: any; // 上次构建耗时秒
}

// GET 索引状态: 各类索引相对当前 HEAD 的新鲜度 (Phase 1 freshness)。
interface IndexStatusResponse {
  headCommit?: any; // 项目仓库当前 HEAD commit
  items?: IndexStatusItem[]; // 各类索引新鲜度
}

// POST /api/v1/jobs/cancel 请求体。
interface JobCancelRequest {
  jobId: string; // 待取消任务 ID
}

// Job 对外视图 (plan §七 字段规范)。
interface JobDTO {
  jobId: string; // 任务 ID
  projectId: string; // 项目 ID
  jobType: string; // 任务类型, 如 index_rebuild:chroma
  status: string; // 任务状态 (JobStatusEnum)
  error?: any; // 失败原因 (仅 Failed 时)
  createdAt: number; // 创建时间 (epoch 秒)
  updatedAt: number; // 更新时间 (epoch 秒)
}

// 提交类接口返回体: 只回 jobId (不阻塞, 前端凭此轮询 detail)。
interface JobIdData {
  jobId: string; // 新建任务 ID
}

// 登录请求。username + password(前端 RSA 加密的 base64, 或明文兼容; 校验后即丢弃)。
interface LoginRequest {
  username: string; // 用户名
  password: string; // 密码 (RSA 加密 base64 或明文)
}

// 登出请求。撤销 refreshToken 对应会话。
interface LogoutRequest {
  refreshToken: string; // refresh token
}

// McpCallUsage 接口
interface McpCallUsage {
  calls?: number;
}

// McpChromaUsage 接口
interface McpChromaUsage {
  agentCalls?: number;
  devCalls?: number;
  agentHits?: number;
  devHits?: number;
}

// McpGraphUsage 接口
interface McpGraphUsage {
  agentCalls?: number;
  devCalls?: number;
}

// McpModelUsage 接口
interface McpModelUsage {
  agentEmbed?: number;
  devEmbed?: number;
  agentRerank?: number;
  devRerank?: number;
}

// McpProjectUsage 接口
interface McpProjectUsage {
  chroma?: McpChromaUsage;
  codegraph?: McpCallUsage;
  graph?: McpGraphUsage;
  model?: McpModelUsage;
  projectId?: string;
}

// McpUsageMetrics 接口
interface McpUsageMetrics {
  chroma?: McpChromaUsage;
  codegraph?: McpCallUsage;
  graph?: McpGraphUsage;
  model?: McpModelUsage;
}

// McpUsageReportResponse 响应数据
interface McpUsageReportResponse {
  last7d?: McpUsageWindow;
  allTime?: McpUsageWindow;
}

// McpUsageWindow 接口
interface McpUsageWindow {
  projects?: McpProjectUsage[];
  total?: McpUsageMetrics;
}

// add/remove/roles 写操作回执。
interface MemberActionResult {
  orgId: string;
  username: string;
  role?: any;
  removed?: boolean;
}

// 加成员 (幂等 upsert; org_admin 管本 org)。role 对齐 MemberRoleEnum。
interface MemberAddRequest {
  code: string; // 组织编码
  username: string; // 成员用户名
  role?: string; // 成员角色 (MemberRoleEnum: viewer|member|admin)
}

// 成员列表项。
interface MemberItem {
  orgId: string;
  username: string;
  role?: string;
}

// 成员列表请求 (POST body)。前端 post() 走 body, 故 code/分页全收 body, 不用 query。
interface MemberListRequest {
  code: string; // 组织编码
  pageNumber?: number; // 页码, 从 1 起
  pageSize?: number; // 每页数量 (上限 200)
}

// MemberRemoveRequest 请求参数
interface MemberRemoveRequest {
  code: string; // 组织编码
  username: string; // 成员用户名
}

// 改成员角色 (org_admin 管本 org)。
interface MemberRoleRequest {
  code: string; // 组织编码
  username: string; // 成员用户名
  role: string; // 新角色 (MemberRoleEnum: viewer|member|admin)
}

// 记忆条目对外视图 (agent /memory 返回的投影)。
interface MemoryItem {
  id: string; // 记忆 ID
  scope: string; // 作用域
  scopeRef: string; // 作用域 ref
  ownerUserId: string; // 写入者 user_id
  content: string; // 记忆内容
  orgId: string; // 所属租户
  kind?: any; // 类型
  topicKey?: any; // 冲突检测键
  isRedline?: boolean; // org 硬约束
  status?: string; // 状态
}

// POST /api/v1/memory 写记忆 (代理 agent /memory)。
interface MemoryWriteRequest {
  scope: string; // org | team | project | personal
  scopeRef?: string; // org='org' / team_id / project_id / user_id; personal 留空
  content: string; // 记忆内容
  kind?: any; // preference | fact | task ...
  topicKey?: any; // 冲突检测键
  ttl?: any; // 存活秒数 (省略=永久)
}

// create/update/status 写操作回执。
interface OrgActionResult {
  code: string;
  status?: string;
}

// 创建组织 (平台超管, 幂等: 重复 code 报错)。
interface OrgCreateRequest {
  code: string; // 组织编码 (org_id slug)
  name: string; // 组织名称
  description?: any; // 描述
}

// 组织列表 / 详情项。
interface OrgItem {
  code: string;
  name: string;
  status?: string;
  description?: any;
}

// 下拉选择项 (code + 中文 label)。
interface OrgSelectionItem {
  code: string;
  name: string;
  status?: string;
}

// 启用 / 禁用组织 (第一版只置状态, 不物理删)。
interface OrgStatusRequest {
  code: string; // 组织编码
  status: string; // 目标状态 (OrgStatusEnum: ACTIVE|DISABLED)
}

// 更新组织 (名称 / 描述; code 不可改)。
interface OrgUpdateRequest {
  code: string; // 组织编码
  name?: any; // 组织名称
  description?: any; // 描述
}

// PageBody 接口
interface PageBody {
  pageNumber?: number; // 页码, 从 1 起
  pageSize?: number; // 每页数量 (上限 200)
}

// PageDepsRequest 请求参数
interface PageDepsRequest {
  pageRef: string;
}

// PageResult_AuditItem_ 接口
interface PageResult_AuditItem_ {
  result?: number;
  message?: string;
  data?: AuditItem[];
  currentPage?: number;
  pageSize?: number;
  total?: number;
  totalPage?: number;
  errors?: ErrorItem[];
  requestId?: any;
}

// PageResult_JobDTO_ 数据传输对象
interface PageResult_JobDTO_ {
  result?: number;
  message?: string;
  data?: JobDTO[];
  currentPage?: number;
  pageSize?: number;
  total?: number;
  totalPage?: number;
  errors?: ErrorItem[];
  requestId?: any;
}

// PageResult_MemberItem_ 接口
interface PageResult_MemberItem_ {
  result?: number;
  message?: string;
  data?: MemberItem[];
  currentPage?: number;
  pageSize?: number;
  total?: number;
  totalPage?: number;
  errors?: ErrorItem[];
  requestId?: any;
}

// PageResult_OrgItem_ 接口
interface PageResult_OrgItem_ {
  result?: number;
  message?: string;
  data?: OrgItem[];
  currentPage?: number;
  pageSize?: number;
  total?: number;
  totalPage?: number;
  errors?: ErrorItem[];
  requestId?: any;
}

// PageResult_ProjectListItem_ 接口
interface PageResult_ProjectListItem_ {
  result?: number;
  message?: string;
  data?: ProjectListItem[];
  currentPage?: number;
  pageSize?: number;
  total?: number;
  totalPage?: number;
  errors?: ErrorItem[];
  requestId?: any;
}

// PageResult_UserItem_ 接口
interface PageResult_UserItem_ {
  result?: number;
  message?: string;
  data?: UserItem[];
  currentPage?: number;
  pageSize?: number;
  total?: number;
  totalPage?: number;
  errors?: ErrorItem[];
  requestId?: any;
}

// load/unload/register 写操作回执。
interface ProjectActionResult {
  code: string;
  loaded: boolean;
  status?: string;
}

// 项目列表 / 详情项 (plan §七 通用字段)。
interface ProjectListItem {
  code: string;
  name: string;
  repoPath?: any;
  description?: any;
  orgId?: any;
  status?: string;
  loaded?: boolean;
}

// 项目列表查询 (POST body, 与前端 ResizableTable 一致)。分页 + 组织/关键词过滤。
interface ProjectListRequest {
  pageNumber?: number; // 页码, 从 1 起
  pageSize?: number; // 每页数量
  orgId?: any; // 按组织过滤 (缺省=当前 org 可见全部)
  keyword?: any; // 按项目编码/名称模糊匹配
}

// ProjectLoadRequest 请求参数
interface ProjectLoadRequest {
  code: string; // 项目编码
}

// 注册项目请求 (plan §八 示例)。code 唯一, register 幂等 (重复 code 报错)。
interface ProjectRegisterRequest {
  code: string; // 项目编码 (project_id slug)
  name: string; // 项目名称
  repoPath?: any; // 项目仓路径
  description?: any; // 描述
  orgId?: any; // 所属组织 (缺省=当前请求 org)
}

// 登录口令加密用 RSA 公钥 (PEM, SubjectPublicKeyInfo)。前端 JSEncrypt setPublicKey 用。
interface PublicKeyInfo {
  publicKey: string;
}

// 刷新请求。refreshToken 换新 token 对 (旧 refresh 轮换失效)。
interface RefreshRequest {
  refreshToken: string; // refresh token
}

// 当前会话信息 (GET session 返回)。
interface SessionInfo {
  username: string;
  orgId: string;
  roles?: string[]; // 会话用户角色 (platform_admin/admin/member/viewer)
}

// 会话摘要 (agent SessionOut 的 web 投影, camelCase)。
interface SessionItem {
  sessionId: string; // 会话 id
  title?: string; // 标题 (首条 user 消息派生)
  messageCount?: number; // 消息条数
  createdAt?: any; // 创建时间 (ISO8601)
  updatedAt?: any; // 最近活跃时间 (ISO8601)
}

// 历史消息 (agent MessageOut 的 web 投影)。assistant 携带工具调用流 steps。
interface SessionMessageItem {
  role: string; // user | assistant
  content?: string; // 消息正文
  steps?: ChatStep[]; // 工具调用流(assistant)
}

// TableUsageRequest 请求参数
interface TableUsageRequest {
  table: string;
}

// 登录 / 刷新返回的 token 对。
interface TokenPair {
  accessToken: string;
  refreshToken: string;
}

// UnifiedGraphEdge 接口
interface UnifiedGraphEdge {
  source?: any;
  target?: any;
  kind?: any;
}

// UnifiedGraphNode 接口
interface UnifiedGraphNode {
  id?: any;
  kind?: any;
  name?: any;
  filePath?: any;
  startLine?: any;
  language?: any;
  meta?: Record<string, any>;
}

// UnifiedGraphResponse 响应数据
interface UnifiedGraphResponse {
  nodes?: UnifiedGraphNode[];
  edges?: UnifiedGraphEdge[];
  nodeCount?: number;
  edgeCount?: number;
}

// UnifiedGraphStatsResponse 响应数据
interface UnifiedGraphStatsResponse {
  nodesByKind?: Record<string, number>;
  edgesByKind?: Record<string, number>;
  totalNodes?: number;
  totalEdges?: number;
}

// create / update / status / password / roles 写操作回执 (不含敏感字段)。
interface UserActionResult {
  username: string;
  status?: string;
}

// 创建用户 (org admin)。密码明文仅入参, service 立即 hash, 绝不落库 (security.md)。
interface UserCreateRequest {
  username: string; // 用户名 (唯一键)
  password: string; // 初始密码 (明文仅入参)
  orgId: string; // 归属组织
  displayName?: any; // 显示名
  email?: any; // 邮箱
  role?: any; // 组织级角色 (viewer|member|admin)
}

// 用户列表 / 详情 / profile 项。绝不含 password / password_hash (security.md)。
interface UserItem {
  username: string;
  orgId: string;
  displayName?: any;
  email?: any;
  status?: string;
  role?: any;
}

// 重置 / 生成初始密码。新明文仅入参, service hash 后落库。
interface UserPasswordResetRequest {
  username: string; // 用户名
  newPassword: string; // 新密码 (明文仅入参)
}

// 变更用户在某 org 的成员角色 (须审计, plan §十五 规则)。
interface UserRolesRequest {
  username: string; // 用户名
  orgId: string; // 组织
  role: string; // viewer | member | admin
}

// 用户选择器项 (label/value, 前端下拉)。
interface UserSelectionItem {
  label: string;
  value: string;
}

// 启用 / 禁用用户。禁用 → service 撤销其所有会话 (plan §十五 规则)。
interface UserStatusRequest {
  username: string; // 用户名
  status: string; // ACTIVE | DISABLED
}

// 更新用户资料 (不含密码 / 状态 / 角色, 各走专用端点)。
interface UserUpdateRequest {
  username: string; // 用户名 (唯一键)
  displayName?: any; // 显示名
  email?: any; // 邮箱
}

// ValidationError 接口
interface ValidationError {
  loc: any[];
  msg: string;
  type: string;
  input?: any;
  ctx?: Record<string, any>;
}

// OrgsGetDetailParams 查询参数
interface OrgsGetDetailParams {
  code: string; // 组织编码
}

// UsersGetDetailParams 查询参数
interface UsersGetDetailParams {
  username: string; // 用户名
}

// ProjectsGetDetailParams 查询参数
interface ProjectsGetDetailParams {
  code: string; // 项目编码
}

// JobsGetDetailParams 查询参数
interface JobsGetDetailParams {
  jobId: string; // 任务 ID
}

// AgentGetSessionsParams 查询参数
interface AgentGetSessionsParams {
  limit?: number;
  offset?: number;
}

// SessionsGetMessagesParams 查询参数
interface SessionsGetMessagesParams {
  sessionId: string; // 会话 id
}

// V1GetMemoryParams 查询参数
interface V1GetMemoryParams {
  scope: string; // org|team|project|personal
  scopeRef?: string; // 该 scope 的 ref; personal 留空(自动用本人)
  limit?: number; // 返回上限
};
    }