// 顶栏用户菜单 —— 单一下拉收纳组织/项目切换 + 退出登录, 放 TabContainer 页签栏右侧插槽。
import { useCallback } from 'react';
import { history, useModel } from '@umijs/max';

import { DownOutlined, LogoutOutlined, UserOutlined } from '@ant-design/icons';
import { Button, Divider, Dropdown } from 'antd';

import OrgSelect from '@/components/Form/Select/OrgSelect';
import ProjectSelect from '@/components/Form/Select/ProjectSelect';
import { postLogout } from '@/services/apis/authapi';

// select 弹层渲染进面板内(而非 body), 防止点选项被外层 Dropdown 当作外部点击而关闭。
const getPanelPopupContainer = (node: HTMLElement): HTMLElement => {
  return node.parentElement ?? document.body;
};

const UserMenu: React.FC = () => {
  const { initialState } = useModel('@@initialState');
  const { currentOrgId } = useModel('org');
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

  // 下拉面板：组织/项目切换(复用封装 Select) + 退出登录。
  // ProjectSelect key 绑 currentOrgId —— 切 org 后 remount, 按新 org 重新 fetch 项目。
  const renderPanel = useCallback(
    (): React.ReactNode => (
      <div className="w-260 p-12 bg-#ffffff rounded-8 shadow-lg">
        <div className="mb-12">
          <div className="mb-4 text-12 text-#8c8c8c">组织</div>
          <OrgSelect getPopupContainer={getPanelPopupContainer} />
        </div>
        <div>
          <div className="mb-4 text-12 text-#8c8c8c">项目</div>
          <ProjectSelect key={currentOrgId} getPopupContainer={getPanelPopupContainer} />
        </div>
        <Divider className="i:my-12" />
        <Button type="text" danger block className="i:justify-start" onClick={handleLogout}>
          <LogoutOutlined /> 退出登录
        </Button>
      </div>
    ),
    [currentOrgId, handleLogout],
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
