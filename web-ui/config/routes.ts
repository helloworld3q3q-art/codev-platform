// 路由 (架构同 stock-admin-web, 页面换成 codev-platform 后端模块)。
export default [
  { path: '/', redirect: '/dashboard' },
  { path: '/user/login', component: './user/login', layout: false },
  { name: '仪表盘', path: '/dashboard', icon: 'DashboardOutlined', component: './dashboard' },
  { name: '项目管理', path: '/projects', icon: 'AppstoreOutlined', component: './projects' },
  { name: '任务中心', path: '/jobs', icon: 'ScheduleOutlined', component: './jobs' },
  // 代码图谱 (codegraph 5 子页)。路由保持扁平 (React Router 嵌套绝对子路径须以父路径开头),
  // "代码图谱" 父分组只在菜单 (src/menus.tsx) 体现, 子路径以 /codegraph/ 前缀归组。
  { name: '节点图谱', path: '/codegraph/graph', icon: 'PartitionOutlined', component: './codegraph/graph' },
  { name: '节点表格', path: '/codegraph/table', icon: 'TableOutlined', component: './codegraph/table' },
  { name: '文件列表', path: '/codegraph/filelist', icon: 'FileOutlined', component: './codegraph/filelist' },
  { name: '文件浏览', path: '/codegraph/files', icon: 'FolderOutlined', component: './codegraph/files' },
  { name: '跨层链路', path: '/codegraph/crosslink', icon: 'ShareAltOutlined', component: './codegraph/crosslink' },
  // 路由保持扁平 (React Router: 嵌套绝对子路径须以父路径开头, /orgs 不能挂 /system 下)。
  // "系统管理" 分组只在菜单 (src/menus.tsx) 体现, 不影响路由结构。
  { name: '组织管理', path: '/orgs', icon: 'TeamOutlined', component: './orgs' },
  { name: '用户管理', path: '/users', icon: 'UserOutlined', component: './users' },
  { path: '*', component: './404' },
];
