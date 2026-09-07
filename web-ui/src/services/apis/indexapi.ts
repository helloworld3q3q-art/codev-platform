import {
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 索引-提交重建任务
export async function postRebuild(data: Partial<API.any>): Promise<API.CommonResult_JobIdData_> {
  return await post<API.CommonResult_JobIdData_>({
    url: `${commonUrl}/api/v1/indexes/rebuild`,
    data,
  });
}

// 索引-各类新鲜度状态
export async function postStatus(): Promise<API.CommonResult_IndexStatusResponse_> {
  return await post<API.CommonResult_IndexStatusResponse_>({
    url: `${commonUrl}/api/v1/indexes/status`,
    data: {},
  });
}

