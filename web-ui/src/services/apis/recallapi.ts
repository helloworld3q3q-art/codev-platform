import {
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 跨lane代码召回-融合 graph + codegraph + vector
export async function postRecallCode(data: Partial<API.RecallCodeRequest>): Promise<API.CommonResult_RecallCodeResponse_> {
  return await post<API.CommonResult_RecallCodeResponse_>({
    url: `${commonUrl}/api/v1/recall/code`,
    data,
  });
}

