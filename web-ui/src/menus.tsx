import {
  AppstoreOutlined,
  BranchesOutlined,
  DashboardOutlined,
  DeploymentUnitOutlined,
  FileOutlined,
  FolderOutlined,
  PartitionOutlined,
  SafetyOutlined,
  ScheduleOutlined,
  TableOutlined,
  TeamOutlined,
  UserOutlined,
} from '@ant-design/icons';
import type { MenuDataItem } from '@ant-design/pro-components';

/**
 * 静态菜单配置（未接入后端权限系统时使用）。
 *
 * 同时被 app.tsx 的 ProLayout 和 TabContainer 复用：
 * - app.tsx：用作 layout.menu.request 的返回
 * - TabContainer：按 path 反查 name 作为页签标题
 */
// 静态菜单 (codev-platform admin 控制台页面)。架构同 stock-admin-web,
// 仅条目换成本平台后端模块; 侧边栏由 config/routes.ts 驱动, 本数组供 app.tsx fallback + TabContainer 反查标题。
export const MENU_ITEMS: MenuDataItem[] = [
  { name: '仪表盘', path: '/dashboard', icon: <DashboardOutlined /> },
  { name: '项目管理', path: '/projects', icon: <AppstoreOutlined /> },
  { name: '任务中心', path: '/jobs', icon: <ScheduleOutlined /> },
  {
    name: '代码图谱',
    path: '/codegraph',
    icon: <PartitionOutlined />,
    children: [
      { name: '节点图谱', path: '/codegraph/graph', icon: <PartitionOutlined /> },
      { name: '节点表格', path: '/codegraph/table', icon: <TableOutlined /> },
      { name: '文件列表', path: '/codegraph/filelist', icon: <FileOutlined /> },
      { name: '文件浏览', path: '/codegraph/files', icon: <FolderOutlined /> },
      { name: '统一图谱', path: '/codegraph/unified', icon: <DeploymentUnitOutlined /> },
      { name: '影响分析', path: '/codegraph/impact', icon: <BranchesOutlined /> },
    ],
  },
  {
    name: '系统管理',
    path: '/system',
    icon: <SafetyOutlined />,
    children: [
      { name: '组织管理', path: '/orgs', icon: <TeamOutlined /> },
      { name: '用户管理', path: '/users', icon: <UserOutlined /> },
    ],
  },
];

/**
 * 按 path 递归查找菜单名称，未命中时返回 undefined。
 *
 * @param items 菜单数组（支持 children 嵌套）
 * @param path 当前路由 pathname（不带 search）
 */
export function findMenuNameByPath(items: MenuDataItem[], path: string): string | undefined {
  for (const item of items) {
    if (item.path === path && typeof item.name === 'string') {
      return item.name;
    }
    if (item.children?.length) {
      const found = findMenuNameByPath(item.children, path);
      if (found) return found;
    }
  }
  return undefined;
}
