import {
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 仪表盘汇总（股票数、关注数、最新交易日、同步状态）
export async function postSummary(): Promise<API.CommonResultDashboardSummaryResponse> {
  return await post<API.CommonResultDashboardSummaryResponse>({
    url: `${commonUrl}/v1/dashboard/summary`,
    data: {},
  });
}

