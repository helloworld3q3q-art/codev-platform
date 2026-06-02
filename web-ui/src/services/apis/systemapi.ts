import {
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 最新交易日市场环境三票详情（dashboard 卡片用）；尚无数据时返回 null
export async function postTodayDetail(): Promise<API.CommonResultMarketRegimeDailyResponse> {
  return await post<API.CommonResultMarketRegimeDailyResponse>({
    url: `${commonUrl}/v1/system/market-regime/today-detail`,
    data: {},
  });
}

// 切换市场环境，影响推荐引擎风险偏好
export async function postMarketRegimeSet(data: Partial<API.MarketRegimeUpdateRequest>): Promise<API.CommonResultMarketRegimeResponse> {
  return await post<API.CommonResultMarketRegimeResponse>({
    url: `${commonUrl}/v1/system/market-regime/set`,
    data,
  });
}

// 市场环境三票历史（最近 N 天），含量化指标明细
export async function postHistory(data: Partial<API.MarketRegimeHistoryRequest>): Promise<API.CommonResultListMarketRegimeDailyResponse> {
  return await post<API.CommonResultListMarketRegimeDailyResponse>({
    url: `${commonUrl}/v1/system/market-regime/history`,
    data,
  });
}

// 查询当前市场环境（牛市/熊市/默认）
export async function postMarketRegimeGet(): Promise<API.CommonResultMarketRegimeResponse> {
  return await post<API.CommonResultMarketRegimeResponse>({
    url: `${commonUrl}/v1/system/market-regime/get`,
    data: {},
  });
}

// 查询全部数据源最新质量评分（按 quality_score 降序）
export async function postDataSourceQuality(): Promise<API.CommonResultListDataSourceQualityResponse> {
  return await post<API.CommonResultListDataSourceQualityResponse>({
    url: `${commonUrl}/v1/system/data-source-quality`,
    data: {},
  });
}

