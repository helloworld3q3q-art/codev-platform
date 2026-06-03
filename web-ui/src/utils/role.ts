// 管理员角色判定 —— 菜单显隐 (menus.tsx) + 页面写操作禁用 (如记忆库 org/team 写) 的**单一口径**。
// userInfo.roles 为后端下发角色字符串数组; 大小写归一, 兼容 admin / org_admin / platform_admin。
// 真正的权限闸在后端 (require_org_role / scope decision), 前端仅做 UX 显隐。
const _ADMIN_ROLES = new Set(['admin', 'org_admin', 'platform_admin']);

export function isAdminRole(roles: string[] | undefined): boolean {
  return Boolean(roles?.some((r) => _ADMIN_ROLES.has(r.toLowerCase())));
}
