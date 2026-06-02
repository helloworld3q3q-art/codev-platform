// 路由 (架构同 stock-admin-web, 页面换成 codev-platform 后端模块)。
export default [
  { path: '/', redirect: '/dashboard' },
  { path: '/user/login', component: './user/login', layout: false },
  { name: '仪表盘', path: '/dashboard', icon: 'DashboardOutlined', component: './dashboard' },
  { name: '项目管理', path: '/projects', icon: 'AppstoreOutlined', component: './projects' },
  { name: '任务中心', path: '/jobs', icon: 'ScheduleOutlined', component: './jobs' },
  { name: '图谱', path: '/graph', icon: 'DeploymentUnitOutlined', component: './graph' },
  { name: '枚举元数据', path: '/enums', icon: 'TableOutlined', component: './enums' },
  // 路由保持扁平 (React Router: 嵌套绝对子路径须以父路径开头, /orgs 不能挂 /system 下)。
  // "系统管理" 分组只在菜单 (src/menus.tsx) 体现, 不影响路由结构。
  { name: '组织管理', path: '/orgs', icon: 'TeamOutlined', component: './orgs' },
  { name: '用户管理', path: '/users', icon: 'UserOutlined', component: './users' },
  { path: '*', component: './404' },
];
