// 顶栏登出 —— 调 POST /api/v1/auth/logout 撤销会话 + 清本地 token, 回登录页。
import { useCallback } from 'react';
import { history, useModel } from '@umijs/max';

import { LogoutOutlined, UserOutlined } from '@ant-design/icons';
import { Dropdown } from 'antd';
import type { MenuProps } from 'antd';

import { postLogout } from '@/services/apis/authapi';

const MENU_ITEMS: MenuProps['items'] = [
  { key: 'logout', icon: <LogoutOutlined />, label: '退出登录' },
];

const LogoutButton: React.FC = () => {
  const { initialState } = useModel('@@initialState');
  const username = initialState?.userInfo?.username ?? '';

  const handleLogout = useCallback(async (): Promise<void> => {
    const refreshToken = localStorage.getItem('refresh_token') ?? undefined;
    try {
      await postLogout({ refreshToken });
    } catch {
      // 撤销失败不阻塞本地登出。
    }
    localStorage.removeItem('auth_token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('user');
    history.replace('/user/login');
  }, []);

  const handleMenuClick = useCallback<NonNullable<MenuProps['onClick']>>(
    (info) => {
      if (info.key === 'logout') {
        handleLogout();
      }
    },
    [handleLogout],
  );

  return (
    <Dropdown menu={{ items: MENU_ITEMS, onClick: handleMenuClick }}>
      <span className="cursor-pointer px-8">
        <UserOutlined /> {username}
      </span>
    </Dropdown>
  );
};

export default LogoutButton;
