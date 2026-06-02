import {
  AlertOutlined,
  ApartmentOutlined,
  ApiOutlined,
  AppstoreOutlined,
  BarChartOutlined,
  BellOutlined,
  ClusterOutlined,
  CodeOutlined,
  ControlOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  DeploymentUnitOutlined,
  ExperimentOutlined,
  FieldTimeOutlined,
  FileOutlined,
  FolderOutlined,
  FundOutlined,
  LineChartOutlined,
  MenuOutlined,
  MonitorOutlined,
  NodeIndexOutlined,
  PartitionOutlined,
  ProjectOutlined,
  SafetyCertificateOutlined,
  SafetyOutlined,
  ScheduleOutlined,
  SettingOutlined,
  ShareAltOutlined,
  StarOutlined,
  SyncOutlined,
  TableOutlined,
  TeamOutlined,
  TrophyOutlined,
  UserOutlined,
  WalletOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import type { MenuDataItem } from '@ant-design/pro-components';

const ICON_MAP: Record<string, React.ReactNode> = {
  AlertOutlined: <AlertOutlined />,
  ApartmentOutlined: <ApartmentOutlined />,
  ApiOutlined: <ApiOutlined />,
  AppstoreOutlined: <AppstoreOutlined />,
  BarChartOutlined: <BarChartOutlined />,
  BellOutlined: <BellOutlined />,
  ClusterOutlined: <ClusterOutlined />,
  CodeOutlined: <CodeOutlined />,
  ControlOutlined: <ControlOutlined />,
  DashboardOutlined: <DashboardOutlined />,
  DatabaseOutlined: <DatabaseOutlined />,
  DeploymentUnitOutlined: <DeploymentUnitOutlined />,
  ExperimentOutlined: <ExperimentOutlined />,
  FieldTimeOutlined: <FieldTimeOutlined />,
  FileOutlined: <FileOutlined />,
  FolderOutlined: <FolderOutlined />,
  FundOutlined: <FundOutlined />,
  LineChartOutlined: <LineChartOutlined />,
  MenuOutlined: <MenuOutlined />,
  MonitorOutlined: <MonitorOutlined />,
  NodeIndexOutlined: <NodeIndexOutlined />,
  PartitionOutlined: <PartitionOutlined />,
  ProjectOutlined: <ProjectOutlined />,
  SafetyCertificateOutlined: <SafetyCertificateOutlined />,
  SafetyOutlined: <SafetyOutlined />,
  ScheduleOutlined: <ScheduleOutlined />,
  SettingOutlined: <SettingOutlined />,
  ShareAltOutlined: <ShareAltOutlined />,
  StarOutlined: <StarOutlined />,
  StockOutlined: <BarChartOutlined />,
  SyncOutlined: <SyncOutlined />,
  TableOutlined: <TableOutlined />,
  TeamOutlined: <TeamOutlined />,
  TrophyOutlined: <TrophyOutlined />,
  UserOutlined: <UserOutlined />,
  WalletOutlined: <WalletOutlined />,
  WarningOutlined: <WarningOutlined />,
};

export function convertMenuTreeToItems(menus: API.MenuTreeResponse[]): MenuDataItem[] {
  return menus
    .filter((m) => m.visible !== false && m.type !== 'BUTTON')
    .map((m) => ({
      name: m.name,
      path: m.path,
      icon: m.icon ? ICON_MAP[m.icon] : undefined,
      children: m.children?.length ? convertMenuTreeToItems(m.children) : undefined,
    }));
}

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
  { name: '项目管理', path: '/projects', icon: <AppstoreOutlined /> },
  { name: '任务中心', path: '/jobs', icon: <ScheduleOutlined /> },
  { name: '图谱', path: '/graph', icon: <DeploymentUnitOutlined /> },
  { name: '枚举元数据', path: '/enums', icon: <TableOutlined /> },
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
