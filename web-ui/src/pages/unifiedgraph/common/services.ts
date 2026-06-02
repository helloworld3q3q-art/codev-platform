// 统一图谱 services 适配层: 把自动生成的 postXxx 包成 fetchXxx, 剥掉 CommonResult 外壳。
// 自动生成代码 (src/services/apis/graphapi.ts) 走自定义 fetch wrapper (自动加 token / 错误处理)。

// 生成器按 method+末段+计数命名: /unified/graph -> postGraph3 (postGraph/postGraph2 已被 codegraph/cross-link 占), /unified/stats -> postStats3。
import { postGraph3, postStats3 } from '@/services/apis/graphapi';

import type { UnifiedGraphResponse, UnifiedGraphStatsResponse } from './types';

export async function fetchUnifiedGraph(): Promise<UnifiedGraphResponse | undefined> {
  const res = await postGraph3();
  return res.data;
}

export async function fetchUnifiedStats(): Promise<UnifiedGraphStatsResponse | undefined> {
  const res = await postStats3();
  return res.data;
}
