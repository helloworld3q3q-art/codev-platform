declare namespace API {
      
  // any 类型定义
type any = any;

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

// CommonResult_CrossLinkGraphResponse_ 响应数据
interface CommonResult_CrossLinkGraphResponse_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_CrossLinkSearchNodesResponse_ 响应数据
interface CommonResult_CrossLinkSearchNodesResponse_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_CrossLinkStatsResponse_ 响应数据
interface CommonResult_CrossLinkStatsResponse_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_CrossLinkTableRefsResponse_ 响应数据
interface CommonResult_CrossLinkTableRefsResponse_ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CommonResult_CrossLinkTablesResponse_ 响应数据
interface CommonResult_CrossLinkTablesResponse_ {
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

// CommonResult_MemberActionResult_ 接口
interface CommonResult_MemberActionResult_ {
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

// CommonResult_list_CrossLinkEndpointLinkItem__ 接口
interface CommonResult_list_CrossLinkEndpointLinkItem__ {
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

// CommonResult_list_UserSelectionItem__ 接口
interface CommonResult_list_UserSelectionItem__ {
  result?: number;
  message?: string;
  data?: any;
  errors?: ErrorItem[];
  requestId?: any;
}

// CrossLinkEndpointLinkItem 接口
interface CrossLinkEndpointLinkItem {
  node?: any;
  kind?: any;
  path?: any;
  line?: any;
  url?: any;
  direction?: any;
  targets?: CrossLinkEndpointTarget[];
  callers?: CrossLinkEndpointTarget[];
}

// CrossLinkEndpointLinkRequest 请求参数
interface CrossLinkEndpointLinkRequest {
  name?: any;
}

// CrossLinkEndpointTarget 接口
interface CrossLinkEndpointTarget {
  name?: any;
  path?: any;
  line?: any;
  url?: any;
  confidence?: any;
  evidence?: any;
}

// CrossLinkGraphEdge 接口
interface CrossLinkGraphEdge {
  source?: any;
  target?: any;
  kind?: any;
}

// CrossLinkGraphNode 接口
interface CrossLinkGraphNode {
  id?: any;
  kind?: any;
  name?: any;
  filePath?: any;
  startLine?: any;
  language?: any;
}

// CrossLinkGraphRequest 请求参数
interface CrossLinkGraphRequest {
  mode?: any;
  kinds?: any;
  excludeKinds?: any;
  rels?: any;
  excludeRels?: any;
  limit?: any;
}

// CrossLinkGraphResponse 响应数据
interface CrossLinkGraphResponse {
  nodes?: CrossLinkGraphNode[];
  edges?: CrossLinkGraphEdge[];
  nodeCount?: number;
  edgeCount?: number;
}

// CrossLinkNodeRef 接口
interface CrossLinkNodeRef {
  name?: any;
  kind?: any;
  path?: any;
  line?: any;
  confidence?: any;
  evidence?: any;
}

// CrossLinkSearchHit 接口
interface CrossLinkSearchHit {
  name?: any;
  kind?: any;
  path?: any;
  line?: any;
  language?: any;
  meta?: Record<string, any>;
}

// CrossLinkSearchNodesRequest 请求参数
interface CrossLinkSearchNodesRequest {
  query?: any;
  kind?: any;
  limit?: any;
}

// CrossLinkSearchNodesResponse 响应数据
interface CrossLinkSearchNodesResponse {
  query?: any;
  kind?: any;
  hits?: CrossLinkSearchHit[];
}

// CrossLinkStatsResponse 响应数据
interface CrossLinkStatsResponse {
  lastBuildAt?: any;
  nodesByKind?: Record<string, number>;
  edgesByRel?: Record<string, number>;
}

// CrossLinkTableRefsRequest 请求参数
interface CrossLinkTableRefsRequest {
  table?: any;
}

// CrossLinkTableRefsResponse 响应数据
interface CrossLinkTableRefsResponse {
  table?: any;
  definers?: CrossLinkNodeRef[];
  javaReaders?: CrossLinkNodeRef[];
  javaWriters?: CrossLinkNodeRef[];
  javaUpdaters?: CrossLinkNodeRef[];
  pythonReaders?: CrossLinkNodeRef[];
  pythonWriters?: CrossLinkNodeRef[];
  pythonUpdaters?: CrossLinkNodeRef[];
}

// CrossLinkTablesResponse 响应数据
interface CrossLinkTablesResponse {
  tables?: string[];
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

// POST /api/v1/indexes/rebuild 请求体。
interface IndexRebuildRequest {
  indexKind?: any; // 索引类型: all / chroma / codegraph / cross_link; 不传默认 all
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

// 登录请求。username + 明文 password (校验后立即丢弃, 不落库)。
interface LoginRequest {
  username: string; // 用户名
  password: string; // 密码 (明文, 仅校验用)
}

// 登出请求。撤销 refreshToken 对应会话。
interface LogoutRequest {
  refreshToken: string; // refresh token
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
  status?: string;
  loaded?: boolean;
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
}

// 刷新请求。refreshToken 换新 token 对 (旧 refresh 轮换失效)。
interface RefreshRequest {
  refreshToken: string; // refresh token
}

// 当前会话信息 (GET session 返回)。
interface SessionInfo {
  username: string;
  orgId: string;
}

// 登录 / 刷新返回的 token 对。
interface TokenPair {
  accessToken: string;
  refreshToken: string;
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

// PostOrgsListParams 查询参数
interface PostOrgsListParams {
  pageNumber?: number; // 页码, 从 1 起
  pageSize?: number; // 每页数量
}

// OrgsGetDetailParams 查询参数
interface OrgsGetDetailParams {
  code: string; // 组织编码
}

// PostMembersListParams 查询参数
interface PostMembersListParams {
  code: string; // 组织编码
  pageNumber?: number; // 页码, 从 1 起
  pageSize?: number; // 每页数量
}

// PostUsersListParams 查询参数
interface PostUsersListParams {
  pageNumber?: number; // 页码, 从 1 起
  pageSize?: number; // 每页数量
}

// UsersGetDetailParams 查询参数
interface UsersGetDetailParams {
  username: string; // 用户名
}

// PostProjectsListParams 查询参数
interface PostProjectsListParams {
  pageNumber?: number; // 页码, 从 1 起
  pageSize?: number; // 每页数量
}

// ProjectsGetDetailParams 查询参数
interface ProjectsGetDetailParams {
  code: string; // 项目编码
}

// JobsGetDetailParams 查询参数
interface JobsGetDetailParams {
  jobId: string; // 任务 ID
};
    }