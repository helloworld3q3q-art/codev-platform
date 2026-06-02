import {
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 查询股票北向持股最近 N 日（按交易日升序）
export async function postNorthBound(data: Partial<API.NorthBoundQueryRequest>): Promise<API.CommonResultListNorthBoundHoldingResponse> {
  return await post<API.CommonResultListNorthBoundHoldingResponse>({
    url: `${commonUrl}/v1/stocks/north-bound`,
    data,
  });
}

