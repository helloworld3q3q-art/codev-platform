import { ProLayoutProps } from '@ant-design/pro-components';

// Ant Design Pro Layout 基础配置：深空科技主题，暗色侧栏 + 渐变头部
const Settings: ProLayoutProps & {
  pwa?: boolean;
  logo?: string;
} = {
  navTheme: 'light',
  colorPrimary: '#6366f1',
  layout: 'side',
  contentWidth: 'Fluid',
  fixedHeader: true,
  fixSiderbar: true,
  colorWeak: false,
  title: 'OpenClaw Stock',
  pwa: false,
  iconfontUrl: '',
  token: {
    colorPrimary: '#6366f1',
    sider: {
      colorMenuBackground: '#ffffff',
      colorMenuItemDivider: '#f1f5f9',
      colorBgMenuItemHover: 'rgba(99, 102, 241, 0.08)',
      colorBgMenuItemSelected: 'rgba(99, 102, 241, 0.12)',
      colorTextMenu: '#475569',
      colorTextMenuItemHover: '#6366f1',
      colorTextMenuActive: '#6366f1',
      colorTextMenuSelected: '#6366f1',
      colorTextMenuTitle: '#0f172a',
    },
    header: {
      colorBgHeader: 'transparent',
      colorTextMenu: '#e2e8f0',
      colorTextMenuActive: '#a5b4fc',
      colorTextMenuSelected: '#a5b4fc',
    },
  },
};

export default Settings;
