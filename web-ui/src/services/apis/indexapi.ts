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

