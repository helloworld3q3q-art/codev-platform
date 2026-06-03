// CodeGraph services 适配层：把自动生成的 postXxx 包成 fetchXxx，剥掉 CommonResult 外壳直接拿 data。
// 自动生成代码（src/services/apis/graphapi.ts）走自定义 fetch wrapper，自动加 token / 错误处理。
// 业务页面统一从这里取数据，避免到处解构 res.data。

import {
  postCodegraphNode,
  postFileTree,
  postGraph,
  postNeighbors,
  postSearch,
} from '@/services/apis/graphapi';

import type {
  FileTreeResponse,
  GraphRequest,
  GraphResponse,
  NeighborsRequest,
  NeighborsResponse,
  NodeDTO,
  SearchRequest,
  SearchResponse,
} from './types';

// ---- codegraph ----

export async function fetchSearch(
  req: Partial<SearchRequest>,
): Promise<SearchResponse | undefined> {
  const res = await postSearch(req);
  return res.data;
}

export async function fetchNode(id: string): Promise<NodeDTO | undefined> {
  const res = await postCodegraphNode({ id });
  return res.data;
}

export async function fetchNeighbors(
  req: Partial<NeighborsRequest>,
): Promise<NeighborsResponse | undefined> {
  const res = await postNeighbors(req);
  return res.data;
}

export async function fetchFileTree(
  prefix?: string,
): Promise<FileTreeResponse | undefined> {
  const res = await postFileTree({ prefix });
  return res.data;
}

export async function fetchGraph(
  req: Partial<GraphRequest> = {},
): Promise<GraphResponse | undefined> {
  const res = await postGraph(req);
  return res.data;
}
