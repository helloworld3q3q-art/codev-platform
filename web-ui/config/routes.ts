// 路由 + 菜单 (与 stock-admin-web config/routes.ts 同构)。
// 业务页面按 codev-platform 后端模块组织: 项目 / 任务 / 图谱 / 枚举。登录走 user/login。
export default [
  {
    path: '/user',
    layout: false,
    routes: [{ name: '登录', path: '/user/login', component: './user/login' }],
  },
  { path: '/', redirect: '/projects' },
  { name: '项目管理', path: '/projects', icon: 'AppstoreOutlined', component: './projects' },
  { name: '任务中心', path: '/jobs', icon: 'ScheduleOutlined', component: './jobs' },
  { name: '图谱', path: '/graph', icon: 'DeploymentUnitOutlined', component: './graph' },
  { name: '枚举元数据', path: '/enums', icon: 'TagsOutlined', component: './enums' },
  { path: '*', layout: false, component: './404' },
];
