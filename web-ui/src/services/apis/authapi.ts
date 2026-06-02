import {
  BasePaginationResponse,
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 修改个人信息（昵称/邮箱/头像）
export async function postUpdate(data: Partial<API.UpdateProfileRequest>): Promise<API.CommonResultVoid> {
  return await post<API.CommonResultVoid>({
    url: `${commonUrl}/v1/auth/profile/update`,
    data,
  });
}

// 获取当前用户可见菜单树（用于前端动态路由）
export async function postMenus(): Promise<API.CommonResultListMenuTreeResponse> {
  return await post<API.CommonResultListMenuTreeResponse>({
    url: `${commonUrl}/v1/auth/menus`,
    data: {},
  });
}

// 获取当前登录用户信息（含角色和权限集）
export async function postAuthMe(): Promise<API.CommonResultUserDetailResponse> {
  return await post<API.CommonResultUserDetailResponse>({
    url: `${commonUrl}/v1/auth/me`,
    data: {},
  });
}

// 更新当前用户的账户总资金（4W 仓位规划基数）
export async function postCapital(data: Partial<API.UpdateCapitalRequest>): Promise<API.CommonResultVoid> {
  return await post<API.CommonResultVoid>({
    url: `${commonUrl}/v1/auth/me/capital`,
    data,
  });
}

// 退出登录
export async function postLogout(): Promise<API.CommonResultBoolean> {
  return await post<API.CommonResultBoolean>({
    url: `${commonUrl}/v1/auth/logout`,
    data: {},
  });
}

// 账号密码登录
export async function postLogin(data: Partial<API.LoginRequest>): Promise<API.CommonResultLoginResponse> {
  return await post<API.CommonResultLoginResponse>({
    url: `${commonUrl}/v1/auth/login`,
    data,
  });
}

// 修改自己的密码
export async function postChangePassword(data: Partial<API.ChangePasswordRequest>): Promise<API.CommonResultVoid> {
  return await post<API.CommonResultVoid>({
    url: `${commonUrl}/v1/auth/changePassword`,
    data,
  });
}

// 查询当前用户账户总资金变更历史
export async function postCapitalHistory(data: Partial<API.CapitalChangeLogPageRequest>): Promise<API.PageResultCapitalChangeLogResponse> {
  return await post<API.PageResultCapitalChangeLogResponse>({
    url: `${commonUrl}/v1/auth/capital-history`,
    data,
  });
}

