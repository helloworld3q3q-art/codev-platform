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

// 节点 kind → 所属层 (用于 KindFilter 按层分组 + "跨层链路"视图)。
export const KIND_LAYER: Record<UnifiedNodeKind, string> = {
  frontend_route: '前端',
  frontend_component: '前端',
  frontend_api_call: '前端',
  backend_endpoint: '后端',
  backend_function: '后端',
  db_table: '数据库',
  db_column: '数据库',
  project: '其他',
  file: '其他',
  wiki_page: '文档/外部',
  jira_issue: '文档/外部',
  feishu_doc: '文档/外部',
  git_commit: '文档/外部',
  pull_request: '文档/外部',
};

// 层展示顺序 (前端 → 后端 → 数据库 是主链路方向)。
export const LAYER_ORDER = ['前端', '后端', '数据库', '文档/外部', '其他'];

export function unifiedLayerOf(kind?: string): string {
  return KIND_LAYER[kind as UnifiedNodeKind] ?? '其他';
}

// 层级聚类偏移: 主链路三层沿 X 轴排开 (数据库 → 后端 → 前端), 每层聚成一个 3D 球团。
// 文档/外部、其他 不参与 (返回 undefined → 自由力导)。复用 unifiedLayerOf 单一真值源。
const CLUSTER_OFFSET: Record<string, number> = {
  数据库: -1,
  后端: 0,
  前端: 1,
};

export function unifiedClusterOf(kind?: string): number | undefined {
  return CLUSTER_OFFSET[unifiedLayerOf(kind)];
}

// 边 kind → 中文标签 (节点详情面板里分组展示关联节点)。
export const EDGE_LABEL: Record<string, string> = {
  contains: '包含',
  imports: '导入',
  calls: '调用',
  renders: '渲染',
  defines_api: '定义接口',
  calls_api: '调用接口',
  implements: '实现',
  reads_table: '读表',
  writes_table: '写表',
  updates_table: '改表',
  defines_column: '定义字段',
  mentions: '提及',
  relates_to: '关联',
  changed_by: '变更于',
};

export function unifiedEdgeLabelOf(kind?: string): string {
  return EDGE_LABEL[kind ?? ''] ?? (kind ?? '关联');
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

// 软边(A1/A2 理解层标注: 角色/业务域),对齐后端 graph/schema.py:SOFT_EDGE_KINDS。
// 这些边不是功能依赖,只是把节点挂到角色/域 hub 上 —— 图上经它们"连通"≠真跨层链路。
// 故图谱里用淡色 + 细线区分,避免被误读成硬依赖(见 roadmap-2026-06-07 前端孤岛分析)。
export const SOFT_EDGE_KINDS = new Set<string>(['plays_role', 'belongs_to_domain']);

export function isSoftEdgeKind(kind?: string): boolean {
  return SOFT_EDGE_KINDS.has(kind ?? '');
}
