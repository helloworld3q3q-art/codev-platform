// 组织管理行类型 + 数据层封装 (转调生成的 service)。
import {
  postCreate,
  postMembersAdd,
  postMembersList,
  postOrgsList,
  postRemove,
  postRoles,
  postStatus,
  postUpdate,
} from '@/services/apis/orgapi';

// 组织列表 / 详情行类型 (对齐后端 OrgItem)。
export type OrgRow = API.OrgItem;

// 成员列表行类型 (对齐后端 MemberItem)。
export type MemberRow = API.MemberItem;

// 创建组织入参 (对齐后端 OrgCreateRequest)。
export type OrgCreateParams = API.OrgCreateRequest;

// 更新组织入参 (对齐后端 OrgUpdateRequest)。
export type OrgUpdateParams = API.OrgUpdateRequest;

// 启用 / 禁用目标状态 (与后端 OrgStatusEnum 字面量一致, 仅作枚举常量引用)。
export const ORG_STATUS_ACTIVE = 'ACTIVE';
export const ORG_STATUS_DISABLED = 'DISABLED';

// 转换 ProTable 查询参数为后端分页 DTO。
// ResizableTable 内部已将 ProTable 的 current 转为 pageNum。
export const convertParams = (params: Record<string, unknown>): Record<string, unknown> => {
  return {
    pageNumber: params.pageNum as number,
    pageSize: params.pageSize as number,
  };
};

// 组织列表请求 (传给 requestWrapper 的 apiFunction)。
export const fetchOrgList = (
  data: Record<string, unknown>,
): Promise<API.PageResult_OrgItem_> => {
  return postOrgsList(data);
};

// 创建组织 (需 platform_admin, 后端会拦, 前端正常调)。
export const createOrg = (
  data: OrgCreateParams,
): Promise<API.CommonResult_OrgActionResult_> => {
  return postCreate(data);
};

// 更新组织 (名称 / 描述; code 不可改)。
export const updateOrg = (
  data: OrgUpdateParams,
): Promise<API.CommonResult_OrgActionResult_> => {
  return postUpdate(data);
};

// 启用 / 禁用组织。
export const setOrgStatus = (
  code: string,
  status: string,
): Promise<API.CommonResult_OrgActionResult_> => {
  return postStatus({ code, status });
};

// 成员列表请求 (传给 requestWrapper 的 apiFunction)。
export const fetchMemberList = (
  data: Record<string, unknown>,
): Promise<API.PageResult_MemberItem_> => {
  return postMembersList(data as Partial<API.PostMembersListParams>);
};

// 添加成员 (幂等 upsert)。
export const addMember = (
  data: API.MemberAddRequest,
): Promise<API.CommonResult_MemberActionResult_> => {
  return postMembersAdd(data);
};

// 改成员角色。
export const setMemberRole = (
  data: API.MemberRoleRequest,
): Promise<API.CommonResult_MemberActionResult_> => {
  return postRoles(data);
};

// 移除成员。
export const removeMember = (
  code: string,
  username: string,
): Promise<API.CommonResult_MemberActionResult_> => {
  return postRemove({ code, username });
};
