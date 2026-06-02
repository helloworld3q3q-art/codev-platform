import {
  BasePaginationResponse,
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 回测交易明细分页查询（按 trade_date 升序，含 triggered_rules 数组）
export async function postTrades(data: Partial<API.BacktestTradePageRequest>): Promise<API.PageResultBacktestTradeResponse> {
  return await post<API.PageResultBacktestTradeResponse>({
    url: `${commonUrl}/v1/backtest/trades`,
    data,
  });
}

// 回测任务分页列表查询（按创建时间倒序，支持 name/status/start_date 过滤）
export async function postBacktestList(data: Partial<API.BacktestRunPageRequest>): Promise<API.PageResultBacktestRunSummaryResponse> {
  return await post<API.PageResultBacktestRunSummaryResponse>({
    url: `${commonUrl}/v1/backtest/list`,
    data,
  });
}

// 回测净值曲线全量（按 trade_date 升序）
export async function postEquityCurve(data: Partial<API.BacktestIdRequest>): Promise<API.CommonResultListBacktestEquityPointResponse> {
  return await post<API.CommonResultListBacktestEquityPointResponse>({
    url: `${commonUrl}/v1/backtest/equity-curve`,
    data,
  });
}

// 回测详情（run + result 联表，含 configJson / metricsJson 原文，不含子表）
export async function postDetail(data: Partial<API.BacktestDetailRequest>): Promise<API.CommonResultBacktestDetailResponse> {
  return await post<API.CommonResultBacktestDetailResponse>({
    url: `${commonUrl}/v1/backtest/detail`,
    data,
  });
}

// 回测单规则贡献全量（按 total_pnl 降序）
export async function postAttribution(data: Partial<API.BacktestIdRequest>): Promise<API.CommonResultListBacktestAttributionResponse> {
  return await post<API.CommonResultListBacktestAttributionResponse>({
    url: `${commonUrl}/v1/backtest/attribution`,
    data,
  });
}

