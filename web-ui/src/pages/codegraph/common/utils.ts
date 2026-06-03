import {
  ApiOutlined,
  BlockOutlined,
  ClusterOutlined,
  CodeOutlined,
  FileOutlined,
  FunctionOutlined,
  ImportOutlined,
  TagOutlined,
} from '@ant-design/icons';

import type { EdgeKind, NodeKind } from './types';

export const NODE_COLOR: Record<NodeKind, string> = {
  class: '#1677ff',
  interface: '#1677ff',
  method: '#52c41a',
  function: '#52c41a',
  field: '#fa8c16',
  variable: '#fa8c16',
  file: '#8c8c8c',
  import: '#722ed1',
  route: '#eb2f96',
  enum: '#13c2c2',
  enum_member: '#13c2c2',
  constant: '#faad14',
  type_alias: '#2f54eb',
  frontend_page: '#13c2c2',
  frontend_api: '#2f54eb',
  backend_endpoint: '#eb2f96',
  java_method: '#52c41a',
  python_method: '#1677ff',
  flyway_migration: '#722ed1',
  table: '#fa8c16',
  column: '#faad14',
};

export const EDGE_COLOR: Record<EdgeKind, string> = {
  calls: '#52c41a',
  contains: '#bfbfbf',
  imports: '#722ed1',
  extends: '#1677ff',
  implements: '#1677ff',
  instantiates: '#fa8c16',
  references: '#fa8c16',
  page_calls_api: '#13c2c2',
  calls_api: '#2f54eb',
  controller_calls_facade: '#eb2f96',
  facade_calls_service: '#52c41a',
  service_calls_mapper: '#52c41a',
  queries_table: '#fa8c16',
  writes_table: '#f5222d',
  reads_table: '#1677ff',
  updates_table: '#faad14',
  defines_table: '#722ed1',
  defines_column: '#bfbfbf',
  calls_method: '#52c41a',
};

export const NODE_SIZE: Record<NodeKind, number> = {
  class: 8,
  interface: 8,
  method: 5,
  function: 5,
  field: 4,
  variable: 4,
  file: 6,
  import: 4,
  route: 9,
  enum: 6,
  enum_member: 4,
  constant: 4,
  type_alias: 5,
  frontend_page: 8,
  frontend_api: 7,
  backend_endpoint: 9,
  java_method: 5,
  python_method: 6,
  flyway_migration: 6,
  table: 10,
  column: 4,
};

export function nodeColorOf(kind?: string): string {
  return NODE_COLOR[kind as NodeKind] ?? '#bfbfbf';
}

export function edgeColorOf(kind?: string): string {
  return EDGE_COLOR[kind as EdgeKind] ?? '#d9d9d9';
}

export function nodeSizeOf(kind?: string): number {
  return NODE_SIZE[kind as NodeKind] ?? 4;
}

export const KIND_ICON: Record<NodeKind, typeof ClusterOutlined> = {
  class: ClusterOutlined,
  interface: ClusterOutlined,
  method: FunctionOutlined,
  function: FunctionOutlined,
  field: TagOutlined,
  variable: TagOutlined,
  file: FileOutlined,
  import: ImportOutlined,
  route: ApiOutlined,
  enum: BlockOutlined,
  enum_member: BlockOutlined,
  constant: TagOutlined,
  type_alias: CodeOutlined,
  frontend_page: FileOutlined,
  frontend_api: ApiOutlined,
  backend_endpoint: ApiOutlined,
  java_method: FunctionOutlined,
  python_method: FunctionOutlined,
  flyway_migration: BlockOutlined,
  table: ClusterOutlined,
  column: TagOutlined,
};

export function iconOf(kind?: string): typeof ClusterOutlined {
  return KIND_ICON[kind as NodeKind] ?? FileOutlined;
}

export const LANG_COLOR: Record<string, string> = {
  java: '#fa8c16',
  python: '#1677ff',
  tsx: '#13c2c2',
  typescript: '#13c2c2',
  javascript: '#fadb14',
};

export function langColorOf(lang?: string): string {
  return LANG_COLOR[(lang ?? '').toLowerCase()] ?? '#8c8c8c';
}

export const LANG_OPTIONS = [
  { label: 'Java', value: 'java' },
  { label: 'Python', value: 'python' },
  { label: 'TSX', value: 'tsx' },
  { label: 'TypeScript', value: 'typescript' },
  { label: 'JavaScript', value: 'javascript' },
];

export const KIND_OPTIONS = [
  { label: 'class', value: 'class' },
  { label: 'interface', value: 'interface' },
  { label: 'method', value: 'method' },
  { label: 'function', value: 'function' },
  { label: 'field', value: 'field' },
  { label: 'variable', value: 'variable' },
  { label: 'file', value: 'file' },
  { label: 'import', value: 'import' },
  { label: 'enum', value: 'enum' },
  { label: 'route', value: 'route' },
  { label: 'constant', value: 'constant' },
  { label: 'type_alias', value: 'type_alias' },
  { label: '前端页面', value: 'frontend_page' },
  { label: '前端接口', value: 'frontend_api' },
  { label: '后端端点', value: 'backend_endpoint' },
  { label: 'Java 方法', value: 'java_method' },
  { label: 'Python 方法', value: 'python_method' },
  { label: 'Flyway 迁移', value: 'flyway_migration' },
  { label: '数据表', value: 'table' },
  { label: '字段', value: 'column' },
];
