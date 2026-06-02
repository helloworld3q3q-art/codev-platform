import {
  BasePaginationResponse,
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 更新用户基本信息
export async function postUpdate(data: Partial<API.UserUpdateRequest>): Promise<API.CommonResultVoid> {
  return await post<API.CommonResultVoid>({
    url: `${commonUrl}/v1/users/update`,
    data,
  });
}

// 重置用户密码（管理员操作，重置后强制改密）
export async function postResetPassword(data: Partial<API.ResetPasswordRequest>): Promise<API.CommonResultVoid> {
  return await post<API.CommonResultVoid>({
    url: `${commonUrl}/v1/users/resetPassword`,
    data,
  });
}

// 分页查询用户列表
export async function postUsersPage(data: Partial<API.UserPageRequest>): Promise<API.PageResultUserPageResponse> {
  return await post<API.PageResultUserPageResponse>({
    url: `${commonUrl}/v1/users/page`,
    data,
  });
}

// 启用用户
export async function postEnable(data: Partial<API.UserToggleRequest>): Promise<API.CommonResultVoid> {
  return await post<API.CommonResultVoid>({
    url: `${commonUrl}/v1/users/enable`,
    data,
  });
}

// 禁用用户
export async function postDisable(data: Partial<API.UserToggleRequest>): Promise<API.CommonResultVoid> {
  return await post<API.CommonResultVoid>({
    url: `${commonUrl}/v1/users/disable`,
    data,
  });
}

// 创建用户
export async function postCreate(data: Partial<API.UserCreateRequest>): Promise<API.CommonResultLong> {
  return await post<API.CommonResultLong>({
    url: `${commonUrl}/v1/users/create`,
    data,
  });
}

// 为用户分配角色（覆盖式）
export async function postAssignRoles(data: Partial<API.AssignRolesRequest>): Promise<API.CommonResultVoid> {
  return await post<API.CommonResultVoid>({
    url: `${commonUrl}/v1/users/assignRoles`,
    data,
  });
}

