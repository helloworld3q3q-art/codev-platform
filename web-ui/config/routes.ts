// 路由 (架构同 stock-admin-web, 页面换成 codev-platform 后端模块)。
export default [
  { path: '/', redirect: '/projects' },
  { path: '/user/login', component: './user/login', layout: false },
  { name: '项目管理', path: '/projects', icon: 'AppstoreOutlined', component: './projects' },
  { name: '任务中心', path: '/jobs', icon: 'ScheduleOutlined', component: './jobs' },
  { name: '图谱', path: '/graph', icon: 'DeploymentUnitOutlined', component: './graph' },
  { name: '枚举元数据', path: '/enums', icon: 'TableOutlined', component: './enums' },
  {
    name: '系统管理',
    path: '/system',
    icon: 'SafetyOutlined',
    routes: [
      { name: '组织管理', path: '/orgs', component: './orgs' },
      { name: '用户管理', path: '/users', component: './users' },
    ],
  },
  { path: '*', component: './404' },
];
