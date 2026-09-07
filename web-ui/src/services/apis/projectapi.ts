import {
  get,
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 项目管理-项目列表
export async function postProjectsList(data: Partial<API.any>): Promise<API.PageResult_ProjectListItem_> {
  return await post<API.PageResult_ProjectListItem_>({
    url: `${commonUrl}/api/v1/projects/list`,
    data,
  });
}

// 项目管理-注册项目
export async function postRegister(data: Partial<API.ProjectRegisterRequest>): Promise<API.CommonResult_ProjectActionResult_> {
  return await post<API.CommonResult_ProjectActionResult_>({
    url: `${commonUrl}/api/v1/projects/register`,
    data,
  });
}

// 项目管理-项目详情
export async function getDetail(data: Partial<API.ProjectsGetDetailParams>): Promise<API.CommonResult_ProjectListItem_> {
  return await get<API.CommonResult_ProjectListItem_>({
    url: `${commonUrl}/api/v1/projects/detail`,
    data,
  });
}

// 项目管理-加载项目
export async function postProjectsLoad(data: Partial<API.ProjectLoadRequest>): Promise<API.CommonResult_ProjectActionResult_> {
  return await post<API.CommonResult_ProjectActionResult_>({
    url: `${commonUrl}/api/v1/projects/load`,
    data,
  });
}

// 项目管理-卸载项目
export async function postUnload(data: Partial<API.ProjectLoadRequest>): Promise<API.CommonResult_ProjectActionResult_> {
  return await post<API.CommonResult_ProjectActionResult_>({
    url: `${commonUrl}/api/v1/projects/unload`,
    data,
  });
}

