// 项目管理行类型 + 数据层封装 (转调生成的 service)。
import {
  postProjectsList,
  postProjectsLoad,
  postRegister,
  postUnload,
} from '@/services/apis/projectapi';

// 项目列表 / 详情行类型 (对齐后端 ProjectListItem)。
export type ProjectRow = API.ProjectListItem;

// 注册项目入参 (对齐后端 ProjectRegisterRequest)。
export type ProjectRegisterParams = API.ProjectRegisterRequest;

// 转换 ProTable 查询参数为后端分页 DTO。
// ResizableTable 内部已将 ProTable 的 current 转为 pageNum。
export const convertParams = (params: Record<string, unknown>): Record<string, unknown> => {
  return {
    pageNumber: params.pageNum as number,
    pageSize: params.pageSize as number,
  };
};

// 列表请求 (传给 requestWrapper 的 apiFunction)。
export const fetchProjectList = (
  data: Record<string, unknown>,
): Promise<API.PageResult_ProjectListItem_> => {
  return postProjectsList(data);
};

// 注册项目。
export const registerProject = (
  data: ProjectRegisterParams,
): Promise<API.CommonResult_ProjectActionResult_> => {
  return postRegister(data);
};

// 加载项目。
export const loadProject = (code: string): Promise<API.CommonResult_ProjectActionResult_> => {
  return postProjectsLoad({ code });
};

// 卸载项目。
export const unloadProject = (code: string): Promise<API.CommonResult_ProjectActionResult_> => {
  return postUnload({ code });
};
