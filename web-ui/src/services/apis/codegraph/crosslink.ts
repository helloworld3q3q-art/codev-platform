import {
  post,
} from '@/utils/fetch';

const commonUrl = 'http://localhost:18082';

// 列出所有业务表名（按字母排序）
export async function postTables(): Promise<CG.CommonResultCrossLinkTablesResponse> {
  return await post<CG.CommonResultCrossLinkTablesResponse>({
    url: `${commonUrl}/v1/cross-link/tables`,
    data: {},
  });
}

// 按表名查 7 类引用清单（Flyway 定义 + Java/Python 读写更新）
export async function postTableRefs(data: Partial<CG.CrossLinkTableRefsRequest>): Promise<CG.CommonResultCrossLinkTableRefsResponse> {
  return await post<CG.CommonResultCrossLinkTableRefsResponse>({
    url: `${commonUrl}/v1/cross-link/table-refs`,
    data,
  });
}

// 构建元数据 + 节点/边分布统计
export async function postStats(): Promise<CG.CommonResultCrossLinkStatsResponse> {
  return await post<CG.CommonResultCrossLinkStatsResponse>({
    url: `${commonUrl}/v1/cross-link/stats`,
    data: {},
  });
}

// 节点名模糊检索（LIKE %query% COLLATE NOCASE，支持 kind 过滤）
export async function postSearchNodes(data: Partial<CG.CrossLinkSearchNodesRequest>): Promise<CG.CommonResultCrossLinkSearchNodesResponse> {
  return await post<CG.CommonResultCrossLinkSearchNodesResponse>({
    url: `${commonUrl}/v1/cross-link/search-nodes`,
    data,
  });
}

// 返回 cross_layer 全图（所有节点 + 所有边）供前端 force-graph 渲染
export async function postGraph(data: Partial<CG.CrossLinkGraphRequest>): Promise<CG.CommonResultCrossLinkGraphResponse> {
  return await post<CG.CommonResultCrossLinkGraphResponse>({
    url: `${commonUrl}/v1/cross-link/graph`,
    data,
  });
}

// 前端 ↔ Java endpoint 双向关联查询（自动判断 name 类型）
export async function postEndpointLink(data: Partial<CG.CrossLinkEndpointLinkRequest>): Promise<CG.CommonResultListCrossLinkEndpointLinkItem> {
  return await post<CG.CommonResultListCrossLinkEndpointLinkItem>({
    url: `${commonUrl}/v1/cross-link/endpoint-link`,
    data,
  });
}

