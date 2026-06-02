import {
  BasePaginationResponse,
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 4W 草稿转为正式交易计划（返回新建计划 ID）
export async function postToTradePlan(data: Partial<API.FourWToTradePlanRequest>): Promise<API.CommonResultLong> {
  return await post<API.CommonResultLong>({
    url: `${commonUrl}/v1/four-w/to-trade-plan`,
    data,
  });
}

// 4W 投资草稿分页查询
export async function postFourWPage(data: Partial<API.FourWPageRequest>): Promise<API.PageResultFourWPlanResponse> {
  return await post<API.PageResultFourWPlanResponse>({
    url: `${commonUrl}/v1/four-w/page`,
    data,
  });
}

// 4W 草稿详情
export async function postDetail(data: Partial<API.IdRequest>): Promise<API.CommonResultFourWPlanResponse> {
  return await post<API.CommonResultFourWPlanResponse>({
    url: `${commonUrl}/v1/four-w/detail`,
    data,
  });
}

// 账户资金摘要（加仓候选 + 减仓关注）
export async function postCapitalSummary(): Promise<API.CommonResultCapitalSummaryResponse> {
  return await post<API.CommonResultCapitalSummaryResponse>({
    url: `${commonUrl}/v1/four-w/capital-summary`,
    data: {},
  });
}

