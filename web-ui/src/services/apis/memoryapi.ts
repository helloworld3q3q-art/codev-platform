import {
  get,
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 记忆-写入
export async function postMemory(data: Partial<API.MemoryWriteRequest>): Promise<API.CommonResult_MemoryItem_> {
  return await post<API.CommonResult_MemoryItem_>({
    url: `${commonUrl}/api/v1/memory`,
    data,
  });
}

// 记忆-列表(按作用域)
export async function getMemory(data: Partial<API.V1GetMemoryParams>): Promise<API.CommonResult_list_MemoryItem__> {
  return await get<API.CommonResult_list_MemoryItem__>({
    url: `${commonUrl}/api/v1/memory`,
    data,
  });
}

