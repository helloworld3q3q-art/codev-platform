import {
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 影子规则 IC 趋势 + 各规则最新胜率（按日期范围 + limit 截断）
export async function postRuleIc(data: Partial<API.ShadowRuleIcRequest>): Promise<API.CommonResultShadowRuleIcResponse> {
  return await post<API.CommonResultShadowRuleIcResponse>({
    url: `${commonUrl}/v1/shadow-recommend/rule-ic`,
    data,
  });
}

// 影子规则 vs 实盘累计 PnL 曲线对比（两条线 SQL 层严格隔离）
export async function postPnlCurve(data: Partial<API.ShadowPnLCurveRequest>): Promise<API.CommonResultShadowPnLCurveResponse> {
  return await post<API.CommonResultShadowPnLCurveResponse>({
    url: `${commonUrl}/v1/shadow-recommend/pnl-curve`,
    data,
  });
}

// 影子推荐对比概览（影子 / 实盘的总数、最新平均 IC、加权胜率）
export async function postOverview(): Promise<API.CommonResultShadowOverviewResponse> {
  return await post<API.CommonResultShadowOverviewResponse>({
    url: `${commonUrl}/v1/shadow-recommend/overview`,
    data: {},
  });
}

// 按分析日查询影子 / 实盘 TOP 10 推荐对比
export async function postByDate(data: Partial<API.ShadowByDateRequest>): Promise<API.CommonResultShadowByDateResponse> {
  return await post<API.CommonResultShadowByDateResponse>({
    url: `${commonUrl}/v1/shadow-recommend/by-date`,
    data,
  });
}

