// 分层记忆页类型 + 常量。

// 记忆行类型 (对齐后端 MemoryItem)。
export type MemoryRow = API.MemoryItem;

// 作用域枚举值 (与后端 scope 字面量一致)。
export type MemoryScope = 'org' | 'team' | 'project' | 'personal';

// scope Segmented 选项。
export const SCOPE_OPTIONS: { label: string; value: MemoryScope }[] = [
  { label: '组织', value: 'org' },
  { label: '团队', value: 'team' },
  { label: '项目', value: 'project' },
  { label: '个人', value: 'personal' },
];

// status Badge 颜色映射 (纯 UI 派生, 非业务枚举, 允许本地常量)。
export const STATUS_BADGE: Record<string, 'success' | 'default' | 'warning'> = {
  active: 'success',
  expired: 'default',
  pending: 'warning',
};

// 写入需要管理员角色的 scope (org/team 写入按角色禁用)。
export const WRITE_ADMIN_SCOPES: MemoryScope[] = ['org', 'team'];

// 列表查询入参。
export interface MemoryListParams {
  scope: MemoryScope;
  scopeRef: string;
  limit: number;
}
