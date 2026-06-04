// 顶栏用户菜单 —— 下拉收纳"切换组织/项目"入口 + 退出登录;组织/项目本体放可拖动浮窗。
import { useCallback, useState } from 'react';
import { history, useModel } from '@umijs/max';

import { ApartmentOutlined, DownOutlined, LogoutOutlined, UserOutlined } from '@ant-design/icons';
import { Button, Divider, Dropdown } from 'antd';

import DraggablePanel from '@/components/DraggablePanel';
import OrgSelect from '@/components/Form/Select/OrgSelect';
import ProjectSelect from '@/components/Form/Select/ProjectSelect';
import { postLogout } from '@/services/apis/authapi';

// select 弹层渲染进面板内(而非 body), 拖动浮窗时跟随、不残留。
const getPanelPopupContainer = (node: HTMLElement): HTMLElement => {
  return node.parentElement ?? document.body;
};

const UserMenu: React.FC = () => {
  const { initialState } = useModel('@@initialState');
  const { currentOrgId } = useModel('org');
  const username = initialState?.userInfo?.username ?? '';
  const [orgPanelOpen, setOrgPanelOpen] = useState<boolean>(false);

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

  const openOrgPanel = useCallback((): void => setOrgPanelOpen(true), []);
  const closeOrgPanel = useCallback((): void => setOrgPanelOpen(false), []);

  // 下拉面板:打开"组织/项目"浮窗入口 + 退出登录。
  const renderPanel = useCallback(
    (): React.ReactNode => (
      <div className="w-200 p-8 bg-#ffffff rounded-8 shadow-lg">
        <Button type="text" block className="i:justify-start" onClick={openOrgPanel}>
          <ApartmentOutlined /> 切换组织/项目
        </Button>
        <Divider className="i:my-8" />
        <Button type="text" danger block className="i:justify-start" onClick={handleLogout}>
          <LogoutOutlined /> 退出登录
        </Button>
      </div>
    ),
    [openOrgPanel, handleLogout],
  );

  return (
    <>
      <Dropdown popupRender={renderPanel} trigger={['click']} placement="bottomRight">
        <span className="flex items-center gap-4 px-8 cursor-pointer select-none">
          <UserOutlined />
          <span>{username}</span>
          <DownOutlined className="text-12" />
        </span>
      </Dropdown>

      {/* 组织/项目:可拖动浮窗。ProjectSelect key 绑 currentOrgId —— 切 org 后 remount 重新 fetch。 */}
      <DraggablePanel
        open={orgPanelOpen}
        onClose={closeOrgPanel}
        title="组织 / 项目"
        width={260}
        defaultPosition={{ x: 100000, y: 64 }}
        storageKey="org-project"
      >
        <div className="mb-12">
          <div className="mb-4 text-12 text-#8c8c8c">组织</div>
          <OrgSelect getPopupContainer={getPanelPopupContainer} />
        </div>
        <div>
          <div className="mb-4 text-12 text-#8c8c8c">项目</div>
          <ProjectSelect key={currentOrgId} getPopupContainer={getPanelPopupContainer} />
        </div>
      </DraggablePanel>
    </>
  );
};

export default UserMenu;
