import {
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 单批次完整 trial 序列(参数组合 + sharpe/calmar/mdd/return 全字段)
export async function postTrials(data: Partial<API.BayesianTrialsRequest>): Promise<API.CommonResultListBayesianTrialResponse> {
  return await post<API.CommonResultListBayesianTrialResponse>({
    url: `${commonUrl}/v1/backtest/bayesian-search/trials`,
    data,
  });
}

// 最近 N 次贝叶斯搜索批次摘要(每批次最佳 trial + trial 数 + 时间)
export async function postBayesianSearchList(data: Partial<API.BayesianSearchListRequest>): Promise<API.CommonResultListBayesianSearchSummaryResponse> {
  return await post<API.CommonResultListBayesianSearchSummaryResponse>({
    url: `${commonUrl}/v1/backtest/bayesian-search/list`,
    data,
  });
}

