import EnumLoader from '@/components/EnumLoader';
import ProjectSelect from '@/components/ProjectSelect';
import TabContainer from '@/components/TabContainer';
import { MENU_ITEMS } from '@/menus';
import type { UserInfo } from '@/models/user';
import { StyleProvider } from '@ant-design/cssinjs';
import type { Settings as LayoutSettings } from '@ant-design/pro-components';
import type { RunTimeLayoutConfig } from '@umijs/max';
import { history } from '@umijs/max';
import { App, ConfigProvider } from 'antd';
import type { ReactNode } from 'react';
import defaultSettings from '../config/defaultSettings';
import antdTheme from './theme/antd';

import 'uno.css';

export async function getInitialState(): Promise<{
  settings?: Partial<LayoutSettings>;
  userInfo?: UserInfo;
}> {
  // 从 localStorage 恢复登录用户信息，供 access.ts 使用
  let userInfo: UserInfo | undefined;
  const stored = localStorage.getItem('user');
  if (stored) {
    try {
      userInfo = JSON.parse(stored) as UserInfo;
    } catch {
      userInfo = undefined;
    }
  }
  return {
    settings: defaultSettings as Partial<LayoutSettings>,
    userInfo,
  };
}

export function rootContainer(container: ReactNode) {
  return (
    <StyleProvider hashPriority="high">
      <ConfigProvider theme={antdTheme} componentSize="middle">
        <App>
          {container}
        </App>
      </ConfigProvider>
    </StyleProvider>
  );
}

// 路径白名单：不需要缓存的页面（KeepAlive）
export function getKeepAlive() {
  return [/^\/(?!(user\/login|system\/404|404$))\/.+/];
}

export const layout: RunTimeLayoutConfig = ({ initialState }) => {
  return {
    onPageChange: () => {
      const path = history.location.pathname;
      const token = localStorage.getItem('auth_token');
      if (!token && path !== '/user/login') {
        history.replace(
          `/user/login?redirect=${encodeURIComponent(path + history.location.search)}`,
        );
      }
      if (token && path === '/user/login') {
        history.replace('/projects');
      }
    },
    // 全局包裹：EnumLoader 应用启动时拉取后端枚举；TabContainer 提供多页签和 KeepAlive
    childrenRender: () => (
      <EnumLoader>
        <TabContainer />
      </EnumLoader>
    ),
    menu: {
      locale: false,
      // codev-platform admin: 静态菜单 (无后端 sys_menu 动态菜单系统; 接入后改回 request 拉取)。
      request: async () => MENU_ITEMS,
    },
    menuHeaderRender: false,
    rightContentRender: false,
    // 顶栏右侧项目选择器 (多租户上下文)。
    actionsRender: () => [<ProjectSelect key="project" />],
    waterMarkProps: undefined,
    ...initialState?.settings,
  };
};
