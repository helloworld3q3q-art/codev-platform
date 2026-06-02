import {
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 推荐入选股票追踪记录（按时间窗口 + 状态）
export async function postTracks(data: Partial<API.RecommendationTrackQueryRequest>): Promise<API.CommonResultListRecommendationTrackResponse> {
  return await post<API.CommonResultListRecommendationTrackResponse>({
    url: `${commonUrl}/v1/recommendations/tracks`,
    data,
  });
}

// 派发推荐追踪刷新任务（RECOMMENDATION_TRACK_REFRESH）到任务总线；同日期幂等，不重复创建
export async function postRefresh(data: Partial<API.RecommendationTrackBootstrapRequest>): Promise<API.CommonResultMapStringObject> {
  return await post<API.CommonResultMapStringObject>({
    url: `${commonUrl}/v1/recommendations/tracks/refresh`,
    data,
  });
}

// 推荐追踪批量补登记（按日期扫描 stock_recommend_result，补 TRACKING 记录）
export async function postBootstrap(data: Partial<API.RecommendationTrackBootstrapRequest>): Promise<API.CommonResultMapStringObject> {
  return await post<API.CommonResultMapStringObject>({
    url: `${commonUrl}/v1/recommendations/tracks/bootstrap`,
    data,
  });
}

// 推荐追踪单条详情 + 持有期间行情序列（前端画价格走势曲线）
export async function postTrackDetail(data: Partial<API.RecommendationTrackDetailRequest>): Promise<API.CommonResultRecommendationTrackDetailResponse> {
  return await post<API.CommonResultRecommendationTrackDetailResponse>({
    url: `${commonUrl}/v1/recommendations/track-detail`,
    data,
  });
}

// 推荐胜率与平均收益汇总（仅统计 CLOSED）
export async function postPerformance(data: Partial<API.RecommendationTrackQueryRequest>): Promise<API.CommonResultRecommendationPerformanceResponse> {
  return await post<API.CommonResultRecommendationPerformanceResponse>({
    url: `${commonUrl}/v1/recommendations/performance`,
    data,
  });
}

// 按 policy_version 分桶的 CLOSED 推荐胜率聚合（用于 sampleprogress 分 policy 详情区块）
export async function postByPolicy(data: Partial<API.RecommendationTrackQueryRequest>): Promise<API.CommonResultRecommendationPolicyBreakdownResponse> {
  return await post<API.CommonResultRecommendationPolicyBreakdownResponse>({
    url: `${commonUrl}/v1/recommendations/performance/by-policy`,
    data,
  });
}

