import {
  BasePaginationResponse,
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 回测持仓明细分页查询（按入场日期升序，用于前端净值曲线）
export async function postPositions(data: Partial<API.BacktestPositionRequest>): Promise<API.PageResultBacktestPositionResponse> {
  return await post<API.PageResultBacktestPositionResponse>({
    url: `${commonUrl}/v1/backtest/runs/positions`,
    data,
  });
}

// 回测任务列表分页查询（按创建时间倒序）
export async function postRunsList(data: Partial<API.BacktestListRequest>): Promise<API.PageResultBacktestRunResponse> {
  return await post<API.PageResultBacktestRunResponse>({
    url: `${commonUrl}/v1/backtest/runs/list`,
    data,
  });
}

// 回测任务详情（含完整 config / 绩效字段）
export async function postDetail(data: Partial<API.BacktestRunIdRequest>): Promise<API.CommonResultBacktestRunResponse> {
  return await post<API.CommonResultBacktestRunResponse>({
    url: `${commonUrl}/v1/backtest/runs/detail`,
    data,
  });
}

// 创建回测任务（派发 BACKTEST_RUN 任务到任务总线，返回 run_id）
export async function postCreate(data: Partial<API.BacktestCreateRequest>): Promise<API.CommonResultMapStringObject> {
  return await post<API.CommonResultMapStringObject>({
    url: `${commonUrl}/v1/backtest/runs/create`,
    data,
  });
}

// 取消回测任务（仅 PENDING 状态可取消）
export async function postCancel(data: Partial<API.BacktestRunIdRequest>): Promise<API.CommonResultMapStringObject> {
  return await post<API.CommonResultMapStringObject>({
    url: `${commonUrl}/v1/backtest/runs/cancel`,
    data,
  });
}

