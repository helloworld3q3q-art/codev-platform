// 路由 (架构同 stock-admin-web, 页面换成 codev-platform 后端模块)。
export default [
  { path: '/', redirect: '/dashboard' },
  { path: '/user/login', component: './user/login', layout: false },
  { name: '仪表盘', path: '/dashboard', icon: 'DashboardOutlined', component: './dashboard' },
  { name: '项目管理', path: '/projects', icon: 'AppstoreOutlined', component: './projects' },
  { name: '任务中心', path: '/jobs', icon: 'ScheduleOutlined', component: './jobs' },
  { name: 'AI 助手', path: '/agent', icon: 'RobotOutlined', component: './agent' },
  { name: '记忆库', path: '/memory', icon: 'BulbOutlined', component: './memory' },
  // 代码图谱。路由保持扁平 (React Router 嵌套绝对子路径须以父路径开头),
  // "代码图谱" 父分组只在菜单 (src/menus.tsx) 体现, 子路径以 /codegraph/ 前缀归组。
  // 跨层链路已并入统一图谱 (2026-06-03 全栈血缘收敛: 按层筛选 + 节点详情面板的关联节点)。
  {
    name: '节点图谱',
    path: '/codegraph/graph',
    icon: 'PartitionOutlined',
    component: './codegraph/graph',
  },
  {
    name: '节点表格',
    path: '/codegraph/table',
    icon: 'TableOutlined',
    component: './codegraph/table',
  },
  {
    name: '文件列表',
    path: '/codegraph/filelist',
    icon: 'FileOutlined',
    component: './codegraph/filelist',
  },
  {
    name: '文件浏览',
    path: '/codegraph/files',
    icon: 'FolderOutlined',
    component: './codegraph/files',
  },
  {
    name: '统一图谱',
    path: '/codegraph/unified',
    icon: 'DeploymentUnitOutlined',
    component: './unifiedgraph',
  },
  // 影响分析页已判低价值(查询=graph 引擎薄包装 + 盲填节点 UX 差, agent impact 工具已覆盖)→ hideInMenu 隐藏出侧边栏。
  // 禁止继续开发该页(见 memory impact-page-frozen); 路由保留作 deep-link, 页面代码不动。
  {
    name: '影响分析',
    path: '/codegraph/impact',
    icon: 'BranchesOutlined',
    component: './impact',
    hideInMenu: true,
  },
  // 路由保持扁平 (React Router: 嵌套绝对子路径须以父路径开头, /orgs 不能挂 /system 下)。
  // "系统管理" 分组只在菜单 (src/menus.tsx) 体现, 不影响路由结构。
  { name: '组织管理', path: '/orgs', icon: 'TeamOutlined', component: './orgs' },
  { name: '用户管理', path: '/users', icon: 'UserOutlined', component: './users' },
  // 接入令牌: PG agent token 签发/列出/吊销, 仅管理员 (后端 require_org_role admin 兜底), 归 "系统管理" 组。
  { name: '接入令牌', path: '/tokens', icon: 'KeyOutlined', component: './tokens' },
  // 审计日志归 "系统管理" 组 (菜单按角色显隐, 见 src/menus.tsx); 路由扁平不挂 /system 下。
  { name: '审计日志', path: '/audit', icon: 'AuditOutlined', component: './system/audit' },
  // Token 用量审计: per-query token+缓存+成本明细 (读 agent_trace), 同 "系统管理" 组, 仅管理员。
  { name: 'Token 用量', path: '/usage', icon: 'FundOutlined', component: './system/usage' },
  { path: '*', component: './404' },
];
