import {
  BasePaginationResponse,
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 回验汇总分页查询（按信号/周期筛选）
export async function postSummaryPage(data: Partial<API.ValidationSummaryRequest>): Promise<API.PageResultValidationSummaryResponse> {
  return await post<API.PageResultValidationSummaryResponse>({
    url: `${commonUrl}/v1/validation/summary/page`,
    data,
  });
}

// 最新回验汇总（5/10/20 日三档命中率，仪表盘用）
export async function postLatest(): Promise<API.CommonResultListValidationSummaryResponse> {
  return await post<API.CommonResultListValidationSummaryResponse>({
    url: `${commonUrl}/v1/validation/summary/latest`,
    data: {},
  });
}

// 回验明细分页查询（逐笔信号命中情况）
export async function postDetailPage(data: Partial<API.ValidationPageRequest>): Promise<API.PageResultValidationDetailResponse> {
  return await post<API.PageResultValidationDetailResponse>({
    url: `${commonUrl}/v1/validation/detail/page`,
    data,
  });
}

