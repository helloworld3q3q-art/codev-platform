import {
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 枚举元数据-查询枚举列表
export async function postEnumsList(data: Partial<API.any>): Promise<API.CommonResult_dict_str__list_EnumItem___> {
  return await post<API.CommonResult_dict_str__list_EnumItem___>({
    url: `${commonUrl}/api/v1/enums/list`,
    data,
  });
}

