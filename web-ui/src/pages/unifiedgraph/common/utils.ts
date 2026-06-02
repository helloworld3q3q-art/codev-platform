// 统一图谱节点 kind → 颜色 / 大小 / 中文标签 映射。
// 按层着色: 前端 (青/蓝) · 后端 (绿/粉) · 数据库 (橙/黄) · 文档外部 (紫/灰)。

import type { UnifiedNodeKind } from './types';

export const KIND_COLOR: Record<UnifiedNodeKind, string> = {
  project: '#595959',
  file: '#8c8c8c',
  frontend_route: '#13c2c2',
  frontend_component: '#36cfc9',
  frontend_api_call: '#2f54eb',
  backend_endpoint: '#eb2f96',
  backend_function: '#52c41a',
  db_table: '#fa8c16',
  db_column: '#faad14',
  wiki_page: '#722ed1',
  jira_issue: '#9254de',
  feishu_doc: '#b37feb',
  git_commit: '#bfbfbf',
  pull_request: '#d3adf7',
};

export const KIND_SIZE: Record<UnifiedNodeKind, number> = {
  project: 12,
  file: 6,
  frontend_route: 9,
  frontend_component: 7,
  frontend_api_call: 6,
  backend_endpoint: 9,
  backend_function: 6,
  db_table: 10,
  db_column: 4,
  wiki_page: 6,
  jira_issue: 5,
  feishu_doc: 5,
  git_commit: 4,
  pull_request: 5,
};

export const KIND_LABEL: Record<UnifiedNodeKind, string> = {
  project: '项目',
  file: '文件',
  frontend_route: '前端路由',
  frontend_component: '前端组件',
  frontend_api_call: '前端接口调用',
  backend_endpoint: '后端端点',
  backend_function: '后端函数',
  db_table: '数据表',
  db_column: '字段',
  wiki_page: 'Wiki 文档',
  jira_issue: 'Jira 任务',
  feishu_doc: '飞书文档',
  git_commit: 'Git 提交',
  pull_request: 'Pull Request',
};

export function unifiedNodeColorOf(kind?: string): string {
  return KIND_COLOR[kind as UnifiedNodeKind] ?? '#bfbfbf';
}

export function unifiedNodeSizeOf(kind?: string): number {
  return KIND_SIZE[kind as UnifiedNodeKind] ?? 5;
}

export function unifiedKindLabelOf(kind?: string): string {
  return KIND_LABEL[kind as UnifiedNodeKind] ?? (kind ?? '未知');
}

// 边按类型粗分色 (与 codegraph 配色风格一致)。
export const EDGE_COLOR: Record<string, string> = {
  contains: '#bfbfbf',
  imports: '#722ed1',
  calls: '#52c41a',
  renders: '#13c2c2',
  defines_api: '#eb2f96',
  calls_api: '#2f54eb',
  implements: '#1677ff',
  reads_table: '#1677ff',
  writes_table: '#f5222d',
  updates_table: '#faad14',
  mentions: '#d9d9d9',
  relates_to: '#d9d9d9',
  changed_by: '#bfbfbf',
};

export function unifiedEdgeColorOf(kind?: string): string {
  return EDGE_COLOR[kind ?? ''] ?? '#d9d9d9';
}
