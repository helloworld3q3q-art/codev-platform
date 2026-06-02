import {
  BasePaginationResponse,
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 更新角色（内置角色不可修改 code）
export async function postUpdate(data: Partial<API.RoleSaveRequest>): Promise<API.CommonResultVoid> {
  return await post<API.CommonResultVoid>({
    url: `${commonUrl}/v1/roles/update`,
    data,
  });
}

// 分页查询角色列表
export async function postRolesPage(data: Partial<API.RolePageRequest>): Promise<API.PageResultRoleResponse> {
  return await post<API.PageResultRoleResponse>({
    url: `${commonUrl}/v1/roles/page`,
    data,
  });
}

// 查询角色已关联的菜单 ID 列表
export async function postMenus(data: Partial<API.RoleMenusRequest>): Promise<API.CommonResultListLong> {
  return await post<API.CommonResultListLong>({
    url: `${commonUrl}/v1/roles/menus`,
    data,
  });
}

// 查询所有有效角色（用于下拉选择）
export async function postRolesList(): Promise<API.CommonResultListRoleResponse> {
  return await post<API.CommonResultListRoleResponse>({
    url: `${commonUrl}/v1/roles/list`,
    data: {},
  });
}

// 删除角色（内置角色不可删除）
export async function postDelete(data: Partial<API.RoleDeleteRequest>): Promise<API.CommonResultVoid> {
  return await post<API.CommonResultVoid>({
    url: `${commonUrl}/v1/roles/delete`,
    data,
  });
}

// 创建角色
export async function postCreate(data: Partial<API.RoleSaveRequest>): Promise<API.CommonResultLong> {
  return await post<API.CommonResultLong>({
    url: `${commonUrl}/v1/roles/create`,
    data,
  });
}

// 为角色分配菜单/权限（覆盖式）
export async function postAssignMenus(data: Partial<API.AssignMenusRequest>): Promise<API.CommonResultVoid> {
  return await post<API.CommonResultVoid>({
    url: `${commonUrl}/v1/roles/assignMenus`,
    data,
  });
}

