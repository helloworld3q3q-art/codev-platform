import type { UserInfo } from '@/models/user';
import { isAdminRole } from '@/utils/role';

export default function access(initialState: { userInfo?: UserInfo } | undefined) {
  const { userInfo } = initialState ?? {};
  const roles = userInfo?.roles ?? [];
  const permissions = userInfo?.permissions ?? [];
  // 管理员口径统一走 isAdminRole(大小写归一, 兼容后端下发 admin/org_admin/platform_admin)。
  const isAdmin = isAdminRole(roles);

  return {
    canAdmin: isAdmin,
    hasRole: (role: string) => roles.includes(role),
    hasPerm: (perm: string) => isAdmin || permissions.includes(perm),
  };
}
