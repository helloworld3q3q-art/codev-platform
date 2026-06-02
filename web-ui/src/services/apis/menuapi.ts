import {
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 更新菜单节点
export async function postUpdate(data: Partial<API.MenuSaveRequest>): Promise<API.CommonResultVoid> {
  return await post<API.CommonResultVoid>({
    url: `${commonUrl}/v1/menus/update`,
    data,
  });
}

// 查询全量菜单树（管理端，含所有状态节点）
export async function postMenusTree(): Promise<API.CommonResultListMenuTreeResponse> {
  return await post<API.CommonResultListMenuTreeResponse>({
    url: `${commonUrl}/v1/menus/tree`,
    data: {},
  });
}

// 删除菜单节点（级联删除子节点和角色关联）
export async function postDelete(data: Partial<API.MenuDeleteRequest>): Promise<API.CommonResultVoid> {
  return await post<API.CommonResultVoid>({
    url: `${commonUrl}/v1/menus/delete`,
    data,
  });
}

// 创建菜单节点
export async function postCreate(data: Partial<API.MenuSaveRequest>): Promise<API.CommonResultLong> {
  return await post<API.CommonResultLong>({
    url: `${commonUrl}/v1/menus/create`,
    data,
  });
}

