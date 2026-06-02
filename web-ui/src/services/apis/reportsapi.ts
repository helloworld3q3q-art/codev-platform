import {
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 推荐归因报告（指定日期或最新一期）
export async function postAttributionLatest(data: Partial<API.AttributionReportRequest>): Promise<API.CommonResultAttributionReportResponse> {
  return await post<API.CommonResultAttributionReportResponse>({
    url: `${commonUrl}/v1/reports/attribution-latest`,
    data,
  });
}

