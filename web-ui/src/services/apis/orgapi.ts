import {
  get,
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 组织管理-组织列表
export async function postOrgsList(data: Partial<API.any>): Promise<API.PageResult_OrgItem_> {
  return await post<API.PageResult_OrgItem_>({
    url: `${commonUrl}/api/v1/orgs/list`,
    data,
  });
}

// 组织管理-创建组织
export async function postCreate(data: Partial<API.OrgCreateRequest>): Promise<API.CommonResult_OrgActionResult_> {
  return await post<API.CommonResult_OrgActionResult_>({
    url: `${commonUrl}/api/v1/orgs/create`,
    data,
  });
}

// 组织管理-组织详情
export async function getDetail(data: Partial<API.OrgsGetDetailParams>): Promise<API.CommonResult_OrgItem_> {
  return await get<API.CommonResult_OrgItem_>({
    url: `${commonUrl}/api/v1/orgs/detail`,
    data,
  });
}

// 组织管理-更新组织
export async function postUpdate(data: Partial<API.OrgUpdateRequest>): Promise<API.CommonResult_OrgActionResult_> {
  return await post<API.CommonResult_OrgActionResult_>({
    url: `${commonUrl}/api/v1/orgs/update`,
    data,
  });
}

// 组织管理-启用禁用
export async function postStatus(data: Partial<API.OrgStatusRequest>): Promise<API.CommonResult_OrgActionResult_> {
  return await post<API.CommonResult_OrgActionResult_>({
    url: `${commonUrl}/api/v1/orgs/status`,
    data,
  });
}

// 组织管理-组织下拉
export async function postSelections(): Promise<API.CommonResult_list_OrgSelectionItem__> {
  return await post<API.CommonResult_list_OrgSelectionItem__>({
    url: `${commonUrl}/api/v1/orgs/selections`,
    data: {},
  });
}

// 组织管理-成员列表
export async function postMembersList(data: Partial<API.MemberListRequest>): Promise<API.PageResult_MemberItem_> {
  return await post<API.PageResult_MemberItem_>({
    url: `${commonUrl}/api/v1/orgs/members/list`,
    data,
  });
}

// 组织管理-添加成员
export async function postMembersAdd(data: Partial<API.MemberAddRequest>): Promise<API.CommonResult_MemberActionResult_> {
  return await post<API.CommonResult_MemberActionResult_>({
    url: `${commonUrl}/api/v1/orgs/members/add`,
    data,
  });
}

// 组织管理-移除成员
export async function postRemove(data: Partial<API.MemberRemoveRequest>): Promise<API.CommonResult_MemberActionResult_> {
  return await post<API.CommonResult_MemberActionResult_>({
    url: `${commonUrl}/api/v1/orgs/members/remove`,
    data,
  });
}

// 组织管理-成员角色
export async function postRoles(data: Partial<API.MemberRoleRequest>): Promise<API.CommonResult_MemberActionResult_> {
  return await post<API.CommonResult_MemberActionResult_>({
    url: `${commonUrl}/api/v1/orgs/members/roles`,
    data,
  });
}

