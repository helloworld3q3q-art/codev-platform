import {
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 按股票代码查询龙虎榜事件（最近 N 日）
export async function postByStock(data: Partial<API.LhbQueryByStockRequest>): Promise<API.CommonResultListLhbEventResponse> {
  return await post<API.CommonResultListLhbEventResponse>({
    url: `${commonUrl}/v1/lhb/by-stock`,
    data,
  });
}

// 按交易日期查询当日全部龙虎榜事件
export async function postByDate(data: Partial<API.LhbQueryByDateRequest>): Promise<API.CommonResultListLhbEventResponse> {
  return await post<API.CommonResultListLhbEventResponse>({
    url: `${commonUrl}/v1/lhb/by-date`,
    data,
  });
}

