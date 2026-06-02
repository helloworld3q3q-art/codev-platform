import {
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 代码图谱-总体统计
export async function postStats(): Promise<API.CommonResult_CodegraphStatsResponse_> {
  return await post<API.CommonResult_CodegraphStatsResponse_>({
    url: `${commonUrl}/api/v1/graph/codegraph/stats`,
    data: {},
  });
}

// 代码图谱-全文检索节点
export async function postSearch(data: Partial<API.CodegraphSearchRequest>): Promise<API.CommonResult_CodegraphSearchResponse_> {
  return await post<API.CommonResult_CodegraphSearchResponse_>({
    url: `${commonUrl}/api/v1/graph/codegraph/search`,
    data,
  });
}

// 代码图谱-节点详情
export async function postCodegraphNode(data: Partial<API.CodegraphNodeRequest>): Promise<API.CommonResult_CodegraphNode_> {
  return await post<API.CommonResult_CodegraphNode_>({
    url: `${commonUrl}/api/v1/graph/codegraph/node`,
    data,
  });
}

// 代码图谱-1 跳邻居
export async function postNeighbors(data: Partial<API.CodegraphNeighborsRequest>): Promise<API.CommonResult_CodegraphNeighborsResponse_> {
  return await post<API.CommonResult_CodegraphNeighborsResponse_>({
    url: `${commonUrl}/api/v1/graph/codegraph/neighbors`,
    data,
  });
}

// 代码图谱-文件树
export async function postFileTree(data: Partial<API.any>): Promise<API.CommonResult_CodegraphFileTreeResponse_> {
  return await post<API.CommonResult_CodegraphFileTreeResponse_>({
    url: `${commonUrl}/api/v1/graph/codegraph/file-tree`,
    data,
  });
}

// 代码图谱-全图加载
export async function postGraph(data: Partial<API.any>): Promise<API.CommonResult_CodegraphGraphResponse_> {
  return await post<API.CommonResult_CodegraphGraphResponse_>({
    url: `${commonUrl}/api/v1/graph/codegraph/graph`,
    data,
  });
}

// 跨层链路-统计
export async function postStats2(): Promise<API.CommonResult_CrossLinkStatsResponse_> {
  return await post<API.CommonResult_CrossLinkStatsResponse_>({
    url: `${commonUrl}/api/v1/graph/cross-link/stats`,
    data: {},
  });
}

// 跨层链路-表清单
export async function postTables(): Promise<API.CommonResult_CrossLinkTablesResponse_> {
  return await post<API.CommonResult_CrossLinkTablesResponse_>({
    url: `${commonUrl}/api/v1/graph/cross-link/tables`,
    data: {},
  });
}

// 跨层链路-表引用清单
export async function postTableRefs(data: Partial<API.CrossLinkTableRefsRequest>): Promise<API.CommonResult_CrossLinkTableRefsResponse_> {
  return await post<API.CommonResult_CrossLinkTableRefsResponse_>({
    url: `${commonUrl}/api/v1/graph/cross-link/table-refs`,
    data,
  });
}

// 跨层链路-前后端 endpoint 关联
export async function postEndpointLink(data: Partial<API.CrossLinkEndpointLinkRequest>): Promise<API.CommonResult_list_CrossLinkEndpointLinkItem__> {
  return await post<API.CommonResult_list_CrossLinkEndpointLinkItem__>({
    url: `${commonUrl}/api/v1/graph/cross-link/endpoint-link`,
    data,
  });
}

// 跨层链路-节点模糊检索
export async function postSearchNodes(data: Partial<API.CrossLinkSearchNodesRequest>): Promise<API.CommonResult_CrossLinkSearchNodesResponse_> {
  return await post<API.CommonResult_CrossLinkSearchNodesResponse_>({
    url: `${commonUrl}/api/v1/graph/cross-link/search-nodes`,
    data,
  });
}

// 跨层链路-全图加载
export async function postGraph2(data: Partial<API.any>): Promise<API.CommonResult_CrossLinkGraphResponse_> {
  return await post<API.CommonResult_CrossLinkGraphResponse_>({
    url: `${commonUrl}/api/v1/graph/cross-link/graph`,
    data,
  });
}

