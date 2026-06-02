import type { UserInfo } from '@/models/user';

export default function access(initialState: { userInfo?: UserInfo } | undefined) {
  const { userInfo } = initialState ?? {};
  const roles = userInfo?.roles ?? [];
  const permissions = userInfo?.permissions ?? [];
  const isAdmin = roles.includes('ADMIN');

  return {
    canAdmin: isAdmin,
    hasRole: (role: string) => roles.includes(role),
    hasPerm: (perm: string) => isAdmin || permissions.includes(perm),
  };
}
