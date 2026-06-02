import {
  post,
} from '@/utils/fetch';

const commonUrl = 'http://localhost:18082';

// 总体统计：文件 / 节点 / 边数 + 多维分布
export async function postStats(): Promise<CG.CommonResultStatsResponse> {
  return await post<CG.CommonResultStatsResponse>({
    url: `${commonUrl}/v1/codegraph/stats`,
    data: {},
  });
}

// 全文检索节点（基于 FTS5，按 prefix 匹配）
export async function postSearch(data: Partial<CG.SearchRequest>): Promise<CG.CommonResultSearchResponse> {
  return await post<CG.CommonResultSearchResponse>({
    url: `${commonUrl}/v1/codegraph/search`,
    data,
  });
}

// 按 id 查询节点详情
export async function postCodegraphNode(data: Partial<CG.NodeRequest>): Promise<CG.CommonResultNodeDTO> {
  return await post<CG.CommonResultNodeDTO>({
    url: `${commonUrl}/v1/codegraph/node`,
    data,
  });
}

// 取节点 1 跳邻居（含中心节点 + 邻居 + 边）
export async function postNeighbors(data: Partial<CG.NeighborsRequest>): Promise<CG.CommonResultNeighborsResponse> {
  return await post<CG.CommonResultNeighborsResponse>({
    url: `${commonUrl}/v1/codegraph/neighbors`,
    data,
  });
}

// 全图加载：返回所有节点 + 所有边（可 limit 截断 + 按 language/kind 过滤）
export async function postGraph(data: Partial<CG.GraphRequest>): Promise<CG.CommonResultGraphResponse> {
  return await post<CG.CommonResultGraphResponse>({
    url: `${commonUrl}/v1/codegraph/graph`,
    data,
  });
}

// 查询已索引文件（可按 path 前缀过滤）
export async function postFileTree(data: Partial<CG.FileTreeRequest>): Promise<CG.CommonResultFileTreeResponse> {
  return await post<CG.CommonResultFileTreeResponse>({
    url: `${commonUrl}/v1/codegraph/file-tree`,
    data,
  });
}

