// 项目管理行类型 + 数据层封装。
// TODO: pnpm run api 生成后改为 `import { ... } from '@/services/apis/projectapi'`,
//       并删除本文件内的 fetch 封装,直接把生成的 post* 方法传给 requestWrapper。
import { post } from '@/utils/fetch';

// 项目列表 / 详情行类型 (对齐后端 ProjectListItem)。
// TODO: pnpm run api 后改为 API.ProjectListItem。
export interface ProjectRow {
  code: string;
  name: string;
  repoPath?: string;
  description?: string;
  status?: string;
  loaded?: boolean;
}

// 注册项目入参 (对齐后端 ProjectRegisterRequest)。
export interface ProjectRegisterParams {
  code: string;
  name: string;
  repoPath?: string;
  description?: string;
}

// 转换 ProTable 查询参数为后端分页 DTO。
// ResizableTable 内部已将 ProTable 的 current 转为 pageNum。
export const convertParams = (params: Record<string, unknown>): Record<string, unknown> => {
  return {
    pageNumber: params.pageNum as number,
    pageSize: params.pageSize as number,
  };
};

// 列表请求 (传给 requestWrapper 的 apiFunction)。
// TODO: pnpm run api 后替换为生成的 postProjectsList。
export const fetchProjectList = (data: Record<string, unknown>): Promise<any> => {
  return post({ url: '/api/v1/projects/list', data });
};

// 注册项目。
// TODO: pnpm run api 后替换为生成的 postProjectsRegister。
export const registerProject = (data: ProjectRegisterParams): Promise<any> => {
  return post({ url: '/api/v1/projects/register', data });
};

// 加载项目。
// TODO: pnpm run api 后替换为生成的 postProjectsLoad。
export const loadProject = (code: string): Promise<any> => {
  return post({ url: '/api/v1/projects/load', data: { code } });
};

// 卸载项目。
// TODO: pnpm run api 后替换为生成的 postProjectsUnload。
export const unloadProject = (code: string): Promise<any> => {
  return post({ url: '/api/v1/projects/unload', data: { code } });
};
