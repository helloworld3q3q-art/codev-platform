// 统一图谱 services 适配层: 把自动生成的 postXxx 包成 fetchXxx, 剥掉 CommonResult 外壳。
// 自动生成代码 (src/services/apis/graphapi.ts) 走自定义 fetch wrapper (自动加 token / 错误处理)。

import { postUnifiedGraph, postUnifiedStats } from '@/services/apis/graphapi';

import type { UnifiedGraphResponse, UnifiedGraphStatsResponse } from './types';

export async function fetchUnifiedGraph(): Promise<UnifiedGraphResponse | undefined> {
  const res = await postUnifiedGraph({});
  return res.data;
}

export async function fetchUnifiedStats(): Promise<UnifiedGraphStatsResponse | undefined> {
  const res = await postUnifiedStats();
  return res.data;
}
