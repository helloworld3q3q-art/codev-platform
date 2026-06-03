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

// 统一图谱-全图加载
export async function postGraph2(): Promise<API.CommonResult_UnifiedGraphResponse_> {
  return await post<API.CommonResult_UnifiedGraphResponse_>({
    url: `${commonUrl}/api/v1/graph/unified/graph`,
    data: {},
  });
}

// 统一图谱-统计
export async function postStats2(): Promise<API.CommonResult_UnifiedGraphStatsResponse_> {
  return await post<API.CommonResult_UnifiedGraphStatsResponse_>({
    url: `${commonUrl}/api/v1/graph/unified/stats`,
    data: {},
  });
}

