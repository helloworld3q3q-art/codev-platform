import {
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 数据健康总览（推荐追踪进度 / 跑批连续性 / 多源最新日期 / 高优告警）
export async function postOverview(data: Partial<API.DataHealthOverviewRequest>): Promise<API.CommonResultDataHealthOverviewResponse> {
  return await post<API.CommonResultDataHealthOverviewResponse>({
    url: `${commonUrl}/v1/data-health/overview`,
    data,
  });
}

// 数据完整性缺口明细（行情已采但指标缺失，含股票名称 / 行业）
export async function postIntegrityGaps(data: Partial<API.DataIntegrityGapRequest>): Promise<API.CommonResultDataIntegrityGapResponse> {
  return await post<API.CommonResultDataIntegrityGapResponse>({
    url: `${commonUrl}/v1/data-health/integrity-gaps`,
    data,
  });
}

