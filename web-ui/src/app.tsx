// Umi Max 运行时 (与 stock-admin-web src/app.tsx 同构, 精简)。
// getInitialState 提供当前用户; layout 提供 ProLayout 配置 + 未登录跳转。
import { history } from '@umijs/max';
import defaultSettings from '../config/defaultSettings';

const LOGIN_PATH = '/user/login';

export interface InitialState {
  name?: string;
  token?: string;
}

export async function getInitialState(): Promise<InitialState> {
  // passthrough 期: 从 localStorage 读登录态 (真 auth 波接入后改为请求 /api/v1/auth/session)。
  const token = localStorage.getItem('auth_token') || undefined;
  const name = localStorage.getItem('user') || undefined;
  return { name, token };
}

export const layout = ({ initialState }: { initialState?: InitialState }) => {
  return {
    title: 'codev-platform 控制台',
    menu: { locale: false },
    ...defaultSettings,
    onPageChange: () => {
      const { pathname } = history.location;
      if (!initialState?.token && pathname !== LOGIN_PATH) {
        history.push(LOGIN_PATH);
      }
    },
  };
};
