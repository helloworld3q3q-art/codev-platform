import {
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 查询业绩预告（stockCode 或 reportPeriod 至少传其一）
export async function postEarningsForecast(data: Partial<API.EarningsForecastQueryRequest>): Promise<API.CommonResultListEarningsForecastResponse> {
  return await post<API.CommonResultListEarningsForecastResponse>({
    url: `${commonUrl}/v1/stocks/earnings-forecast`,
    data,
  });
}

