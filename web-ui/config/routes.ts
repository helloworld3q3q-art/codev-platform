// 路由 (架构同 stock-admin-web, 页面换成 codev-platform 后端模块)。
// 业务页面已删, 这里只保留 admin 控制台自己的页面 + 登录/404。
export default [
  { path: '/', redirect: '/projects' },
  { path: '/user/login', component: './user/login', layout: false },
  { name: '项目管理', path: '/projects', icon: 'AppstoreOutlined', component: './projects' },
  { name: '任务中心', path: '/jobs', icon: 'ScheduleOutlined', component: './jobs' },
  { name: '图谱', path: '/graph', icon: 'DeploymentUnitOutlined', component: './graph' },
  { name: '枚举元数据', path: '/enums', icon: 'TableOutlined', component: './enums' },
  { path: '*', component: './404' },
];
