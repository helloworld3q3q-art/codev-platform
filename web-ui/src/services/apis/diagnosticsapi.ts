import {
  BasePaginationResponse,
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 全组合绩效（仪表盘综合表现）
export async function postPortfolio(): Promise<API.CommonResultPerformanceReportResponse> {
  return await post<API.CommonResultPerformanceReportResponse>({
    url: `${commonUrl}/v1/diagnostics/performance/portfolio`,
    data: {},
  });
}

// 策略绩效分页查询（可按股票代码筛选）
export async function postPerformancePage(data: Partial<API.DiagnosticsPageRequest>): Promise<API.PageResultPerformanceReportResponse> {
  return await post<API.PageResultPerformanceReportResponse>({
    url: `${commonUrl}/v1/diagnostics/performance/page`,
    data,
  });
}

// 因子 IC 分页查询
export async function postFactorIcPage(data: Partial<API.DiagnosticsPageRequest>): Promise<API.PageResultFactorIcResponse> {
  return await post<API.PageResultFactorIcResponse>({
    url: `${commonUrl}/v1/diagnostics/factor-ic/page`,
    data,
  });
}

// 最新因子 IC 健康状态（IC>0.03 且 IR>0.5 为健康）
export async function postLatest(): Promise<API.CommonResultFactorIcResponse> {
  return await post<API.CommonResultFactorIcResponse>({
    url: `${commonUrl}/v1/diagnostics/factor-ic/latest`,
    data: {},
  });
}

