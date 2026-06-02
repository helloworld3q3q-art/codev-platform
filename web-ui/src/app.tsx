import EnumLoader from '@/components/EnumLoader';
import LogoutButton from '@/components/LogoutButton';
import OrgSelect from '@/components/Form/Select/OrgSelect';
import ProjectSelect from '@/components/Form/Select/ProjectSelect';
import TabContainer from '@/components/TabContainer';
import { MENU_ITEMS } from '@/menus';
import { getSession } from '@/services/apis/authapi';
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

function readStoredUser(): UserInfo | undefined {
  const stored = localStorage.getItem('user');
  if (!stored) {
    return undefined;
  }
  try {
    return JSON.parse(stored) as UserInfo;
  } catch {
    return undefined;
  }
}

export async function getInitialState(): Promise<{
  settings?: Partial<LayoutSettings>;
  userInfo?: UserInfo;
}> {
  // 有 token 则向后端校验会话(getSession), 拿到当前用户; 失败回退本地存储(dev 容错)。
  let userInfo: UserInfo | undefined = readStoredUser();
  if (localStorage.getItem('auth_token')) {
    try {
      const res = await getSession();
      if (res.data?.username) {
        userInfo = { username: res.data.username };
        localStorage.setItem('user', JSON.stringify(userInfo));
        // 当前组织默认取会话 org (供 fetch 注入 X-Org-Id); 未手动切换前以此为准。
        if (res.data.orgId && !localStorage.getItem('current_org')) {
          localStorage.setItem('current_org', res.data.orgId);
        }
      }
    } catch {
      // 401 已由 fetch 拦截器清 token + 跳登录; 其它错误保留本地用户(后端临时不可用容错)。
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
        history.replace('/dashboard');
      }
    },
    // 全局包裹：EnumLoader 拉枚举；顶部工具条(组织/项目选择器+登出)+ TabContainer 多页签。
    // 选择器放内容区工具条 (而非 ProLayout header rightContentRender) —— 后者在本 side 布局 +
    // rightContentRender:false 既有约定下不渲染, 工具条放这里一定可见。
    childrenRender: () => (
      <EnumLoader>
        <div className="flex items-center justify-end gap-12 px-16 py-8 bg-#ffffff">
          <OrgSelect />
          <ProjectSelect />
          <LogoutButton />
        </div>
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
    waterMarkProps: undefined,
    ...initialState?.settings,
  };
};
