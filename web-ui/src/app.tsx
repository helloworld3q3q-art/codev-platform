import EnumLoader from '@/components/EnumLoader';
import TabContainer from '@/components/TabContainer';
import { MENU_ITEMS } from '@/menus';
import { getSession } from '@/services/apis/authapi';
import { postProjectsList } from '@/services/apis/projectapi';
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

// 启动即初始化项目上下文: 未选则取当前 org 第一个项目写 localStorage,
// 保证各页首个请求就带 X-Project-Id(组织/项目选择器在懒渲染的用户菜单里, 不能靠它初始化)。
async function ensureCurrentProject(): Promise<void> {
  if (localStorage.getItem('current_project')) {
    return;
  }
  try {
    const projRes = await postProjectsList({ pageNumber: 1, pageSize: 1 });
    const firstProject = projRes.data?.[0]?.code;
    if (firstProject) {
      localStorage.setItem('current_project', firstProject);
    }
  } catch {
    // 拉项目失败不阻塞启动
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
        await ensureCurrentProject();
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
    // 全局包裹：EnumLoader 拉枚举 + TabContainer 多页签。
    // 组织/项目选择器 + 登出已收进 TabContainer 页签栏右侧的用户菜单(UserMenu), 不再单列工具条。
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
    waterMarkProps: undefined,
    ...initialState?.settings,
  };
};
