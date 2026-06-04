// 顶栏用户菜单 —— 用户名下拉 + 退出登录。组织/项目已独立为右下角悬浮钮(OrgProjectFab)。
import { useCallback } from 'react';
import { history, useModel } from '@umijs/max';

import { DownOutlined, LogoutOutlined, UserOutlined } from '@ant-design/icons';
import { Button, Dropdown } from 'antd';

import { postLogout } from '@/services/apis/authapi';

const UserMenu: React.FC = () => {
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

  const renderPanel = useCallback(
    (): React.ReactNode => (
      <div className="w-160 p-8 bg-#ffffff rounded-8 shadow-lg">
        <Button type="text" danger block className="i:justify-start" onClick={handleLogout}>
          <LogoutOutlined /> 退出登录
        </Button>
      </div>
    ),
    [handleLogout],
  );

  return (
    <Dropdown popupRender={renderPanel} trigger={['click']} placement="bottomRight">
      <span className="flex items-center gap-4 px-8 cursor-pointer select-none">
        <UserOutlined />
        <span>{username}</span>
        <DownOutlined className="text-12" />
      </span>
    </Dropdown>
  );
};

export default UserMenu;
