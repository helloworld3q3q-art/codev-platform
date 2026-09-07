// 用户管理行类型 + 数据层封装 (转调生成的 service)。
import { postOrgsList } from '@/services/apis/orgapi';
import {
  postCreate,
  postReset,
  postRoles,
  postStatus,
  postUpdate,
  postUsersList,
} from '@/services/apis/userapi';

// 用户列表 / 详情行类型 (对齐后端 UserItem)。
export type UserRow = API.UserItem;

// 状态 Badge 颜色映射 (纯 UI 派生, 非业务枚举, 允许本地常量)。
export const STATUS_BADGE: Record<string, 'success' | 'default' | 'error'> = {
  ACTIVE: 'success',
  DISABLED: 'default',
  LOCKED: 'error',
};

// 角色 Tag 颜色 (纯 UI 映射; 角色文案走 useModel('enum').getFormattedEnums('MemberRoleEnum'))。
export const ROLE_COLOR: Record<string, string> = {
  admin: 'red',
  member: 'blue',
  viewer: 'default',
};

// 转换 ProTable 查询参数为后端分页 DTO。
// ResizableTable 内部已将 ProTable 的 current 转为 pageNum。
export const convertParams = (params: Record<string, unknown>): Record<string, unknown> => {
  return {
    pageNumber: params.pageNum as number,
    pageSize: params.pageSize as number,
  };
};

// 列表请求 (传给 requestWrapper 的 apiFunction)。
export const fetchUserList = (
  data: Record<string, unknown>,
): Promise<API.PageResult_UserItem_> => {
  return postUsersList(data);
};

// 组织下拉选项 (创建用户 / 改角色须选 org)。
export const fetchOrgOptions = async (): Promise<{ label: string; value: string }[]> => {
  const res = await postOrgsList({ pageNumber: 1, pageSize: 200 });
  const list = res.data ?? [];
  return list.map((item) => ({
    label: item.name ?? item.code ?? '',
    value: item.code ?? '',
  }));
};

// 创建用户。
export const createUser = (
  data: API.UserCreateRequest,
): Promise<API.CommonResult_UserActionResult_> => {
  return postCreate(data);
};

// 更新用户资料 (不含密码 / 状态 / 角色)。
export const updateUser = (
  data: API.UserUpdateRequest,
): Promise<API.CommonResult_UserActionResult_> => {
  return postUpdate(data);
};

// 启用 / 禁用用户。
export const setUserStatus = (
  username: string,
  status: string,
): Promise<API.CommonResult_UserActionResult_> => {
  return postStatus({ username, status });
};

// 重置密码。
export const resetUserPassword = (
  username: string,
  newPassword: string,
): Promise<API.CommonResult_UserActionResult_> => {
  return postReset({ username, newPassword });
};

// 变更用户在某 org 的成员角色。
export const setUserRole = (
  data: API.UserRolesRequest,
): Promise<API.CommonResult_UserActionResult_> => {
  return postRoles(data);
};
