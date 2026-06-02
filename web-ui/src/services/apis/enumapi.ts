import {
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 查询枚举列表（不传 enumType 则返回全部枚举）
export async function postEnumsList(data: Partial<API.EnumListRequest>): Promise<API.CommonResultMapStringListEnumItemDTO> {
  return await post<API.CommonResultMapStringListEnumItemDTO>({
    url: `${commonUrl}/v1/admin/enums/list`,
    data,
  });
}

