import {
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 查询 5 大核心指数最新行情（沪深300/上证/深证/创业板/科创50）
export async function postLatest(): Promise<API.CommonResultListMarketIndexLatestResponse> {
  return await post<API.CommonResultListMarketIndexLatestResponse>({
    url: `${commonUrl}/v1/market-index/latest`,
    data: {},
  });
}

// 查询单指数最近 N 天历史走势
export async function postHistory(data: Partial<API.MarketIndexHistoryRequest>): Promise<API.CommonResultListMarketIndexLatestResponse> {
  return await post<API.CommonResultListMarketIndexLatestResponse>({
    url: `${commonUrl}/v1/market-index/history`,
    data,
  });
}

