import {
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 接入令牌-签发
export async function postIssue(data: Partial<API.TokenIssueRequest>): Promise<API.CommonResult_TokenIssueResult_> {
  return await post<API.CommonResult_TokenIssueResult_>({
    url: `${commonUrl}/api/v1/tokens/issue`,
    data,
  });
}

// 接入令牌-列表
export async function postTokensList(data: Partial<API.any>): Promise<API.CommonResult_list_TokenItem__> {
  return await post<API.CommonResult_list_TokenItem__>({
    url: `${commonUrl}/api/v1/tokens/list`,
    data,
  });
}

// 接入令牌-吊销
export async function postRevoke(data: Partial<API.TokenRevokeRequest>): Promise<API.CommonResult_TokenActionResult_> {
  return await post<API.CommonResult_TokenActionResult_>({
    url: `${commonUrl}/api/v1/tokens/revoke`,
    data,
  });
}

