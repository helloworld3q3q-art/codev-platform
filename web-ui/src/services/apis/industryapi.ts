import {
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 单行业近 N 日轮动趋势（rank 序列 + 评分变化 + 轮动状态，前端折线图用）
export async function postRotationTrend(data: Partial<API.IndustryRotationTrendRequest>): Promise<API.CommonResultListIndustryRotationTrendResponse> {
  return await post<API.CommonResultListIndustryRotationTrendResponse>({
    url: `${commonUrl}/v1/industries/rotation-trend`,
    data,
  });
}

// 行业景气度最新排名（每行业取最新一日，按 rank 升序）
export async function postRanking(data: Partial<API.IndustryRankingQueryRequest>): Promise<API.CommonResultListIndustryScoreResponse> {
  return await post<API.CommonResultListIndustryScoreResponse>({
    url: `${commonUrl}/v1/industries/ranking`,
    data,
  });
}

// 行业龙头股 top N(按市值降序,N2 2026-05-23)
export async function postLeadingStocks(data: Partial<API.IndustryLeadingStocksRequest>): Promise<API.CommonResultListIndustryLeadingStockResponse> {
  return await post<API.CommonResultListIndustryLeadingStockResponse>({
    url: `${commonUrl}/v1/industries/leading-stocks`,
    data,
  });
}

// 单行业景气度历史曲线
export async function postByName(data: Partial<API.IndustryDetailQueryRequest>): Promise<API.CommonResultListIndustryScoreResponse> {
  return await post<API.CommonResultListIndustryScoreResponse>({
    url: `${commonUrl}/v1/industries/by-name`,
    data,
  });
}

