import {
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 列出全部数据源商用合规台账（9 个数据源）
export async function postComplianceList(): Promise<API.CommonResultListDataSourceComplianceResponse> {
  return await post<API.CommonResultListDataSourceComplianceResponse>({
    url: `${commonUrl}/v1/data-source/compliance/list`,
    data: {},
  });
}

