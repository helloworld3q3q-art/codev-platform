import {
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 样本门控进度概览（CLOSED 严格定义 + Wilson 区间 + Track 4/5 阶段判定；可选 mode 分桶）
export async function postOverview(data: Partial<API.SampleProgressOverviewRequest>): Promise<API.CommonResultSampleProgressOverviewResponse> {
  return await post<API.CommonResultSampleProgressOverviewResponse>({
    url: `${commonUrl}/v1/sample-progress/overview`,
    data,
  });
}

// 样本门控进度历史（CLOSED 累计 + 胜率 + Wilson 区间按日趋势，默认近 90 天）
export async function postHistory(data: Partial<API.SampleProgressHistoryRequest>): Promise<API.CommonResultSampleProgressHistoryResponse> {
  return await post<API.CommonResultSampleProgressHistoryResponse>({
    url: `${commonUrl}/v1/sample-progress/history`,
    data,
  });
}

