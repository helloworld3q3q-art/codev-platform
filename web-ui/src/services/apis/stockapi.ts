import {
  BasePaginationResponse,
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 查询股票估值趋势（最近 60 条 PE/PB/PS）
export async function postValuation(data: Partial<API.StockCodeRequest>): Promise<API.CommonResultListStockValuationResponse> {
  return await post<API.CommonResultListStockValuationResponse>({
    url: `${commonUrl}/v1/stocks/valuation`,
    data,
  });
}

// 查询日线行情（支持日期范围，默认最近 90 天）
export async function postQuotes(data: Partial<API.StockQuoteRequest>): Promise<API.CommonResultListStockQuoteDailyResponse> {
  return await post<API.CommonResultListStockQuoteDailyResponse>({
    url: `${commonUrl}/v1/stocks/quotes`,
    data,
  });
}

// 股票列表分页查询（含最新行情和分析结果）
export async function postStocksPage(data: Partial<API.StockPageRequest>): Promise<API.PageResultStockPageItemResponse> {
  return await post<API.PageResultStockPageItemResponse>({
    url: `${commonUrl}/v1/stocks/page`,
    data,
  });
}

// 查询融资融券历史（图表 + 表格，最近 60 ��）
export async function postMarginTradingHistory(data: Partial<API.MarginTradingHistoryRequest>): Promise<API.CommonResultMarginTradingHistoryResponse> {
  return await post<API.CommonResultMarginTradingHistoryResponse>({
    url: `${commonUrl}/v1/stocks/margin-trading-history`,
    data,
  });
}

// 查询当日盘中实时报价序列
export async function postIntraday(data: Partial<API.StockCodeRequest>): Promise<API.CommonResultListStockIntradayQuoteResponse> {
  return await post<API.CommonResultListStockIntradayQuoteResponse>({
    url: `${commonUrl}/v1/stocks/intraday`,
    data,
  });
}

// 查询收盘后分钟线聚合的盘中分段数据
export async function postIntradaySessions(data: Partial<API.StockCodeRequest>): Promise<API.CommonResultListStockIntradaySessionResponse> {
  return await post<API.CommonResultListStockIntradaySessionResponse>({
    url: `${commonUrl}/v1/stocks/intraday-sessions`,
    data,
  });
}

// 查询股票技术指标（最近 60 条 MA/MACD/RSI/布林带）
export async function postIndicators(data: Partial<API.StockCodeRequest>): Promise<API.CommonResultListStockIndicatorDailyResponse> {
  return await post<API.CommonResultListStockIndicatorDailyResponse>({
    url: `${commonUrl}/v1/stocks/indicators`,
    data,
  });
}

// 查询股票资金流向（最近 30 条主力净流入）
export async function postFundFlow(data: Partial<API.StockCodeRequest>): Promise<API.CommonResultListStockFundFlowResponse> {
  return await post<API.CommonResultListStockFundFlowResponse>({
    url: `${commonUrl}/v1/stocks/fund-flow`,
    data,
  });
}

// 查询股票资金流历史（柱状图 + 累计净流入折线 + 方向汇总）
export async function postFundFlowHistory(data: Partial<API.FundFlowHistoryRequest>): Promise<API.CommonResultFundFlowHistoryResponse> {
  return await post<API.CommonResultFundFlowHistoryResponse>({
    url: `${commonUrl}/v1/stocks/fund-flow-history`,
    data,
  });
}

// 查询股票财务快照（最近 4 个季报 ROE/ROA 等）
export async function postFinancial(data: Partial<API.StockCodeRequest>): Promise<API.CommonResultListStockFinancialResponse> {
  return await post<API.CommonResultListStockFinancialResponse>({
    url: `${commonUrl}/v1/stocks/financial`,
    data,
  });
}

// 股票详情（基础信息 + 最新分析结果）
export async function postDetail(data: Partial<API.StockCodeRequest>): Promise<API.CommonResultStockDetailResponse> {
  return await post<API.CommonResultStockDetailResponse>({
    url: `${commonUrl}/v1/stocks/detail`,
    data,
  });
}

// 股票详情一站式聚合（12 个维度并发拉取，子调用失败不影响整体）
export async function postDetailFull(data: Partial<API.StockCodeRequest>): Promise<API.CommonResultStockDetailFullResponse> {
  return await post<API.CommonResultStockDetailFullResponse>({
    url: `${commonUrl}/v1/stocks/detail-full`,
    data,
  });
}

// 查询筹码分布（基于近 lookback 天日线行情服务端计算，含分桶 + 关键水位）
export async function postChipsDistribution(data: Partial<API.ChipsDistributionRequest>): Promise<API.CommonResultChipsDistributionResponse> {
  return await post<API.CommonResultChipsDistributionResponse>({
    url: `${commonUrl}/v1/stocks/chips-distribution`,
    data,
  });
}

// 查询最新分析规则信号明细
export async function postSignals(data: Partial<API.StockCodeRequest>): Promise<API.CommonResultListAnalysisSignalDTO> {
  return await post<API.CommonResultListAnalysisSignalDTO>({
    url: `${commonUrl}/v1/stocks/analysis/signals`,
    data,
  });
}

// 查询股票历史分析结果分页
export async function postAnalysisPage(data: Partial<API.StockAnalysisPageRequest>): Promise<API.PageResultStockAnalysisResponse> {
  return await post<API.PageResultStockAnalysisResponse>({
    url: `${commonUrl}/v1/stocks/analysis/page`,
    data,
  });
}

// 查询股票最新分析结果
export async function postLatest(data: Partial<API.StockCodeRequest>): Promise<API.CommonResultStockAnalysisResponse> {
  return await post<API.CommonResultStockAnalysisResponse>({
    url: `${commonUrl}/v1/stocks/analysis/latest`,
    data,
  });
}

