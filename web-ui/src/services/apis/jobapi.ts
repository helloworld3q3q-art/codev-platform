import {
  get,
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 长任务-查询任务详情
export async function getDetail(data: Partial<API.JobsGetDetailParams>): Promise<API.CommonResult_JobDTO_> {
  return await get<API.CommonResult_JobDTO_>({
    url: `${commonUrl}/api/v1/jobs/detail`,
    data,
  });
}

// 长任务-取消任务
export async function postCancel(data: Partial<API.JobCancelRequest>): Promise<API.CommonResult_JobDTO_> {
  return await post<API.CommonResult_JobDTO_>({
    url: `${commonUrl}/api/v1/jobs/cancel`,
    data,
  });
}

// 长任务-列表(按当前项目过滤)
export async function postJobsList(data: Partial<API.any>): Promise<API.PageResult_JobDTO_> {
  return await post<API.PageResult_JobDTO_>({
    url: `${commonUrl}/api/v1/jobs/list`,
    data,
  });
}

