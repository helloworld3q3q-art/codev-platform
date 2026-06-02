import {
  get,
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 认证-登录口令加密公钥
export async function getPublicKey(): Promise<API.CommonResult_PublicKeyInfo_> {
  return await get<API.CommonResult_PublicKeyInfo_>({
    url: `${commonUrl}/api/v1/auth/public-key`,
  });
}

// 认证-登录
export async function postLogin(data: Partial<API.LoginRequest>): Promise<API.CommonResult_TokenPair_> {
  return await post<API.CommonResult_TokenPair_>({
    url: `${commonUrl}/api/v1/auth/login`,
    data,
  });
}

// 认证-登出
export async function postLogout(data: Partial<API.LogoutRequest>): Promise<API.CommonResult_NoneType_> {
  return await post<API.CommonResult_NoneType_>({
    url: `${commonUrl}/api/v1/auth/logout`,
    data,
  });
}

// 认证-刷新令牌
export async function postRefresh(data: Partial<API.RefreshRequest>): Promise<API.CommonResult_TokenPair_> {
  return await post<API.CommonResult_TokenPair_>({
    url: `${commonUrl}/api/v1/auth/token/refresh`,
    data,
  });
}

// 认证-当前会话
export async function getSession(): Promise<API.CommonResult_SessionInfo_> {
  return await get<API.CommonResult_SessionInfo_>({
    url: `${commonUrl}/api/v1/auth/session`,
  });
}

