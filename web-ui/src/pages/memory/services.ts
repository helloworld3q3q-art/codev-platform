// memory 数据层: 把生成的 postXxx/getXxx 包成 fetchXxx, 剥 CommonResult 外壳直接拿 data。
import { getMemory, postMemory } from '@/services/apis/memoryapi';

import type { MemoryListParams, MemoryRow } from './types';

// 按作用域拉记忆列表。
export async function fetchMemoryList(params: MemoryListParams): Promise<MemoryRow[]> {
  const res = await getMemory(params);
  return (res.data as MemoryRow[] | undefined) ?? [];
}

// 写入一条记忆。
export async function createMemory(
  data: Partial<API.MemoryWriteRequest>,
): Promise<API.CommonResult_MemoryItem_> {
  return postMemory(data);
}
