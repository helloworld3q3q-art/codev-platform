import {
  BasePaginationResponse,
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 切换 position_sizing.mode（仅 formula/fixed，自动写审计日志）
export async function postPositionSizingModeSet(data: Partial<API.PositionSizingModeRequest>): Promise<API.CommonResultPositionSizingModeResponse> {
  return await post<API.CommonResultPositionSizingModeResponse>({
    url: `${commonUrl}/v1/configuration/position-sizing-mode/set`,
    data,
  });
}

// 查询当前 position_sizing.mode + 关联仓位上下限 + fixed tiers
export async function postPositionSizingModeGet(data: Partial<API.PositionSizingModeGetRequest>): Promise<API.CommonResultPositionSizingModeResponse> {
  return await post<API.CommonResultPositionSizingModeResponse>({
    url: `${commonUrl}/v1/configuration/position-sizing-mode/get`,
    data,
  });
}

// 获取失效预案预设选项（与 Python FourW 常量对齐）
export async function postWhatifOptions(): Promise<API.CommonResultListWhatIfOptionResponse> {
  return await post<API.CommonResultListWhatIfOptionResponse>({
    url: `${commonUrl}/v1/config/whatif-options`,
    data: {},
  });
}

// 变更交易计划状态（DRAFT→ACTIVE→CLOSED）
export async function postUpdateStatus(data: Partial<API.TradePlanStatusRequest>): Promise<API.CommonResultVoid> {
  return await post<API.CommonResultVoid>({
    url: `${commonUrl}/v1/config/trade-plans/update-status`,
    data,
  });
}

// 保存交易计划（新增或更新）
export async function postTradePlansSave(data: Partial<API.TradePlanSaveRequest>): Promise<API.CommonResultVoid> {
  return await post<API.CommonResultVoid>({
    url: `${commonUrl}/v1/config/trade-plans/save`,
    data,
  });
}

// 交易计划分页查询
export async function postTradePlansPage(data: Partial<API.SimplePageRequest>): Promise<API.PageResultTradePlanResponse> {
  return await post<API.PageResultTradePlanResponse>({
    url: `${commonUrl}/v1/config/trade-plans/page`,
    data,
  });
}

// 从推荐结果生成交易计划草稿
export async function postFromRecommend(data: Partial<API.TradePlanFromRecommendRequest>): Promise<API.CommonResultMapStringObject> {
  return await post<API.CommonResultMapStringObject>({
    url: `${commonUrl}/v1/config/trade-plans/from-recommend`,
    data,
  });
}

// 删除交易计划
export async function postDelete(data: Partial<API.IdRequest>): Promise<API.CommonResultVoid> {
  return await post<API.CommonResultVoid>({
    url: `${commonUrl}/v1/config/trade-plans/delete`,
    data,
  });
}

// 保存系统参数
export async function postSystemConfigSave(data: Partial<API.SystemConfigSaveRequest>): Promise<API.CommonResultVoid> {
  return await post<API.CommonResultVoid>({
    url: `${commonUrl}/v1/config/system-config/save`,
    data,
  });
}

// 系统参数分页查询
export async function postSystemConfigPage(data: Partial<API.SimplePageRequest>): Promise<API.PageResultMapStringObject> {
  return await post<API.PageResultMapStringObject>({
    url: `${commonUrl}/v1/config/system-config/page`,
    data,
  });
}

// 更新任务参数（仅 PENDING 状态可修改）
export async function postUpdate(data: Partial<API.SyncJobUpdateRequest>): Promise<API.CommonResultMapStringObject> {
  return await post<API.CommonResultMapStringObject>({
    url: `${commonUrl}/v1/config/sync-jobs/update`,
    data,
  });
}

// 触发同步任务（登记 PENDING 任务，由 Python worker 执行）
export async function postTrigger(data: Partial<API.TaskTriggerRequest>): Promise<API.CommonResultMapStringObject> {
  return await post<API.CommonResultMapStringObject>({
    url: `${commonUrl}/v1/config/sync-jobs/trigger`,
    data,
  });
}

// 停止任务（向 RUNNING 任务发送取消信号，Python worker 轮询后退出）
export async function postSyncJobsStop(data: Partial<API.IdRequest>): Promise<API.CommonResultMapStringObject> {
  return await post<API.CommonResultMapStringObject>({
    url: `${commonUrl}/v1/config/sync-jobs/stop`,
    data,
  });
}

// 重新运行（复制原任务类型和场景，创建新 PENDING 任务）
export async function postRerun(data: Partial<API.IdRequest>): Promise<API.CommonResultMapStringObject> {
  return await post<API.CommonResultMapStringObject>({
    url: `${commonUrl}/v1/config/sync-jobs/rerun`,
    data,
  });
}

// 同步任务分页查询
export async function postSyncJobsPage(data: Partial<API.SimplePageRequest>): Promise<API.PageResultMapStringObject> {
  return await post<API.PageResultMapStringObject>({
    url: `${commonUrl}/v1/config/sync-jobs/page`,
    data,
  });
}

// 查询任务状态快照
export async function postCheck(data: Partial<API.IdRequest>): Promise<API.CommonResultSyncJobStatusResponse> {
  return await post<API.CommonResultSyncJobStatusResponse>({
    url: `${commonUrl}/v1/config/sync-jobs/check`,
    data,
  });
}

// 保存股票池（新增或更新）
export async function postStockPoolsSave(data: Partial<API.StockPoolSaveRequest>): Promise<API.CommonResultVoid> {
  return await post<API.CommonResultVoid>({
    url: `${commonUrl}/v1/config/stock-pools/save`,
    data,
  });
}

// 股票池分页查询
export async function postStockPoolsPage(data: Partial<API.SimplePageRequest>): Promise<API.PageResultMapStringObject> {
  return await post<API.PageResultMapStringObject>({
    url: `${commonUrl}/v1/config/stock-pools/page`,
    data,
  });
}

// 股票池详情
export async function postDetail(data: Partial<API.IdRequest>): Promise<API.CommonResultMapStringObject> {
  return await post<API.CommonResultMapStringObject>({
    url: `${commonUrl}/v1/config/stock-pools/detail`,
    data,
  });
}

// 删除股票池
export async function postDelete2(data: Partial<API.IdRequest>): Promise<API.CommonResultVoid> {
  return await post<API.CommonResultVoid>({
    url: `${commonUrl}/v1/config/stock-pools/delete`,
    data,
  });
}

// 保存股票池明细（新增时自动检查行情数据，≥30 天则立即派发针对该股的分析任务）
export async function postStockPoolItemsSave(data: Partial<API.StockPoolItemSaveRequest>): Promise<API.CommonResultStockPoolItemSaveResponse> {
  return await post<API.CommonResultStockPoolItemSaveResponse>({
    url: `${commonUrl}/v1/config/stock-pool-items/save`,
    data,
  });
}

// 股票池明细分页查询
export async function postStockPoolItemsPage(data: Partial<API.StockPoolItemPageRequest>): Promise<API.PageResultMapStringObject> {
  return await post<API.PageResultMapStringObject>({
    url: `${commonUrl}/v1/config/stock-pool-items/page`,
    data,
  });
}

// 删除股票池明细
export async function postDelete3(data: Partial<API.IdRequest>): Promise<API.CommonResultVoid> {
  return await post<API.CommonResultVoid>({
    url: `${commonUrl}/v1/config/stock-pool-items/delete`,
    data,
  });
}

// 获取买入理由预设选项（双语）
export async function postSignalExplanations(): Promise<API.CommonResultListSignalExplanationResponse> {
  return await post<API.CommonResultListSignalExplanationResponse>({
    url: `${commonUrl}/v1/config/signal-explanations`,
    data: {},
  });
}

// 报告分页查询（按日期倒序）
export async function postReportsPage(data: Partial<API.StockReportPageRequest>): Promise<API.PageResultStockReportResponse> {
  return await post<API.PageResultStockReportResponse>({
    url: `${commonUrl}/v1/config/reports/page`,
    data,
  });
}

// 报告详情（含完整 JSON 正文）
export async function postDetail2(data: Partial<API.IdRequest>): Promise<API.CommonResultStockReportResponse> {
  return await post<API.CommonResultStockReportResponse>({
    url: `${commonUrl}/v1/config/reports/detail`,
    data,
  });
}

// 保存推荐规则（新增或更新）
export async function postRecommendRulesSave(data: Partial<API.RecommendRuleSaveRequest>): Promise<API.CommonResultVoid> {
  return await post<API.CommonResultVoid>({
    url: `${commonUrl}/v1/config/recommend-rules/save`,
    data,
  });
}

// 推荐规则分页查询
export async function postRecommendRulesPage(data: Partial<API.SimplePageRequest>): Promise<API.PageResultMapStringObject> {
  return await post<API.PageResultMapStringObject>({
    url: `${commonUrl}/v1/config/recommend-rules/page`,
    data,
  });
}

// 删除推荐规则
export async function postDelete4(data: Partial<API.IdRequest>): Promise<API.CommonResultVoid> {
  return await post<API.CommonResultVoid>({
    url: `${commonUrl}/v1/config/recommend-rules/delete`,
    data,
  });
}

// 最新推荐结果分页查询
export async function postLatestPage(data: Partial<API.SimplePageRequest>): Promise<API.PageResultRecommendResultResponse> {
  return await post<API.PageResultRecommendResultResponse>({
    url: `${commonUrl}/v1/config/recommend-results/latest/page`,
    data,
  });
}

// 系统状态汇总（占位接口，暂返回空对象）
export async function postLatest(): Promise<API.CommonResultMapStringObject> {
  return await post<API.CommonResultMapStringObject>({
    url: `${commonUrl}/v1/config/recommend-results/latest`,
    data: {},
  });
}

// 系统状态汇总（占位接口，暂返回空对象）
export async function postSummary(): Promise<API.CommonResultMapStringObject> {
  return await post<API.CommonResultMapStringObject>({
    url: `${commonUrl}/v1/config/system-status/summary`,
    data: {},
  });
}

// 告警分页查询
export async function postAlertsPage(data: Partial<API.AlertPageRequest>): Promise<API.PageResultMapStringObject> {
  return await post<API.PageResultMapStringObject>({
    url: `${commonUrl}/v1/config/alerts/page`,
    data,
  });
}

// 处理告警（标记已处理或未处理）
export async function postHandle(data: Partial<API.AlertHandleRequest>): Promise<API.CommonResultVoid> {
  return await post<API.CommonResultVoid>({
    url: `${commonUrl}/v1/config/alerts/handle`,
    data,
  });
}

