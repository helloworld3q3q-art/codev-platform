import {
  get,
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 影响分析-改动跨层影响报告
export async function postImpact(data: Partial<API.ImpactRequest>): Promise<API.CommonResult_ImpactReportResponse_> {
  return await post<API.CommonResult_ImpactReportResponse_>({
    url: `${commonUrl}/api/v1/reports/impact`,
    data,
  });
}

// 影响分析-表被谁使用
export async function postTableUsage(data: Partial<API.TableUsageRequest>): Promise<API.CommonResult_GraphQueryResponse_> {
  return await post<API.CommonResult_GraphQueryResponse_>({
    url: `${commonUrl}/api/v1/reports/table-usage`,
    data,
  });
}

// 影响分析-前端页依赖
export async function postPageDependencies(data: Partial<API.PageDepsRequest>): Promise<API.CommonResult_GraphQueryResponse_> {
  return await post<API.CommonResult_GraphQueryResponse_>({
    url: `${commonUrl}/api/v1/reports/page-dependencies`,
    data,
  });
}

// 影响分析-端点被谁调用
export async function postApiCallers(data: Partial<API.ApiCallersRequest>): Promise<API.CommonResult_GraphQueryResponse_> {
  return await post<API.CommonResult_GraphQueryResponse_>({
    url: `${commonUrl}/api/v1/reports/api-callers`,
    data,
  });
}

// MCP 调用分析-每项目+合计(7天/全时段, agent/dev 分桶 + 自部署模型)
export async function getMcpUsage(): Promise<API.CommonResult_McpUsageReportResponse_> {
  return await get<API.CommonResult_McpUsageReportResponse_>({
    url: `${commonUrl}/api/v1/reports/mcp-usage`,
  });
}

// agent token 用量-总量+按模型+最近明细(7天/全时段, 含缓存率与估算成本)
export async function getAgentUsage(): Promise<API.CommonResult_AgentUsageReportResponse_> {
  return await get<API.CommonResult_AgentUsageReportResponse_>({
    url: `${commonUrl}/api/v1/reports/agent-usage`,
  });
}

