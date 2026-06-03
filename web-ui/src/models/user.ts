import { useCallback, useState } from 'react';

import { isAdminRole } from '@/utils/role';

export interface UserInfo {
  username?: string;
  userName?: string;
  userId?: number;
  displayName?: string;
  roles?: string[];
  permissions?: string[];
  [key: string]: unknown;
}

export default () => {
  const [userInfo, setUserInfo] = useState<UserInfo>(() => {
    const user = localStorage.getItem('user');
    if (user) {
      try {
        return JSON.parse(user) as UserInfo;
      } catch {
        return {};
      }
    }
    return {};
  });

  const setUser = useCallback((user: UserInfo) => {
    setUserInfo(user);
    localStorage.setItem('user', JSON.stringify(user));
  }, []);

  const clearUser = useCallback(() => {
    setUserInfo({});
    localStorage.removeItem('user');
  }, []);

  const hasRole = useCallback(
    (role: string) => userInfo.roles?.includes(role) ?? false,
    [userInfo.roles],
  );

  const hasPerm = useCallback(
    (perm: string) => {
      if (isAdminRole(userInfo.roles)) return true;  // admin 旁路, 口径同 access.ts/menus
      return userInfo.permissions?.includes(perm) ?? false;
    },
    [userInfo.roles, userInfo.permissions],
  );

  return { userInfo, setUser, clearUser, hasRole, hasPerm };
};
