import {
  get,
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 用户管理-当前用户
export async function getProfile(): Promise<API.CommonResult_UserItem_> {
  return await get<API.CommonResult_UserItem_>({
    url: `${commonUrl}/api/v1/users/profile`,
  });
}

// 用户管理-用户列表
export async function postUsersList(data: Partial<API.PostUsersListParams>): Promise<API.PageResult_UserItem_> {
  return await post<API.PageResult_UserItem_>({
    url: `${commonUrl}/api/v1/users/list`,
    data,
  });
}

// 用户管理-创建用户
export async function postCreate(data: Partial<API.UserCreateRequest>): Promise<API.CommonResult_UserActionResult_> {
  return await post<API.CommonResult_UserActionResult_>({
    url: `${commonUrl}/api/v1/users/create`,
    data,
  });
}

// 用户管理-用户详情
export async function getDetail(data: Partial<API.UsersGetDetailParams>): Promise<API.CommonResult_UserItem_> {
  return await get<API.CommonResult_UserItem_>({
    url: `${commonUrl}/api/v1/users/detail`,
    data,
  });
}

// 用户管理-更新用户
export async function postUpdate(data: Partial<API.UserUpdateRequest>): Promise<API.CommonResult_UserActionResult_> {
  return await post<API.CommonResult_UserActionResult_>({
    url: `${commonUrl}/api/v1/users/update`,
    data,
  });
}

// 用户管理-启用禁用
export async function postStatus(data: Partial<API.UserStatusRequest>): Promise<API.CommonResult_UserActionResult_> {
  return await post<API.CommonResult_UserActionResult_>({
    url: `${commonUrl}/api/v1/users/status`,
    data,
  });
}

// 用户管理-重置密码
export async function postReset(data: Partial<API.UserPasswordResetRequest>): Promise<API.CommonResult_UserActionResult_> {
  return await post<API.CommonResult_UserActionResult_>({
    url: `${commonUrl}/api/v1/users/password/reset`,
    data,
  });
}

// 用户管理-角色授权
export async function postRoles(data: Partial<API.UserRolesRequest>): Promise<API.CommonResult_UserActionResult_> {
  return await post<API.CommonResult_UserActionResult_>({
    url: `${commonUrl}/api/v1/users/roles`,
    data,
  });
}

// 用户管理-用户选择器
export async function getSelections(): Promise<API.CommonResult_list_UserSelectionItem__> {
  return await get<API.CommonResult_list_UserSelectionItem__>({
    url: `${commonUrl}/api/v1/users/selections`,
  });
}

