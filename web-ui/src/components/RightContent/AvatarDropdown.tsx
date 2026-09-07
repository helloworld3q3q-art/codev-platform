import { postLogout } from '@/services/apis/authapi';
import { LogoutOutlined, SettingOutlined, UserOutlined } from '@ant-design/icons';
import { useTabContainer } from '@jlogi/ui';
import { history, useModel } from '@umijs/max';
import { Spin } from 'antd';
import { createStyles } from 'antd-style';
import { stringify } from 'querystring';
import React, { useCallback } from 'react';
import { flushSync } from 'react-dom';
import HeaderDropdown from '../HeaderDropdown';

export type GlobalHeaderRightProps = {
  menu?: boolean;
  children?: React.ReactNode;
};

export const AvatarName = () => {
  const { userInfo } = useModel('user');
  const currentUser = userInfo || {};
  return <span className="anticon">{currentUser?.userName}</span>;
};

const useStyles = createStyles(({ token }) => {
  return {
    action: {
      display: 'flex',
      height: '48px',
      marginLeft: 'auto',
      overflow: 'hidden',
      alignItems: 'center',
      padding: '0 8px',
      cursor: 'pointer',
      borderRadius: token.borderRadius,
      '&:hover': {
        backgroundColor: token.colorBgTextHover,
      },
    },
  };
});

export const AvatarDropdown: React.FC<GlobalHeaderRightProps> = ({ menu, children }) => {
  const { userInfo, setUser, clearUser } = useModel('user');
  const { clearEnums } = useModel('enum');
  const { closeAllTabs } = useTabContainer();

  // 退出登录，并保留当前页面作为重定向目标
  const loginOut = async () => {
    await postLogout({});
    clearUser();
    clearEnums();
    closeAllTabs();

    const { search, pathname } = window.location;
    const urlParams = new URL(window.location.href).searchParams;
    const redirect = urlParams.get('redirect');
    if (window.location.pathname !== '/user/login' && !redirect) {
      history.replace({
        pathname: '/user/login',
        search: stringify({
          redirect: pathname + search,
        }),
      });
    }
  };

  const { styles } = useStyles();
  const onMenuClick = useCallback(
    (event: any) => {
      const { key } = event;
      if (key === 'logout') {
        flushSync(() => {
          setUser({});
        });
        loginOut();
        return;
      }
      history.push(`/account/${key}`);
    },
    [setUser],
  );

  const loading = (
    <span className={styles.action}>
      <Spin
        size="small"
        style={{
          marginLeft: 8,
          marginRight: 8,
        }}
      />
    </span>
  );

  if (!userInfo) {
    return loading;
  }

  const currentUser = userInfo;
  if (!currentUser || !currentUser.userName) {
    return loading;
  }

  const menuItems = [
    ...(menu
      ? [
        {
          key: 'center',
          icon: <UserOutlined />,
          label: '个人中心',
        },
        {
          key: 'settings',
          icon: <SettingOutlined />,
          label: '个人设置',
        },
        {
          type: 'divider' as const,
        },
      ]
      : []),
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: '退出登录',
    },
  ];

  return (
    <HeaderDropdown
      menu={{
        selectedKeys: [],
        onClick: onMenuClick,
        items: menuItems,
      }}
    >
      {children}
    </HeaderDropdown>
  );
};
