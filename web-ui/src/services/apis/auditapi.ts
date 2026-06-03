import {
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 访问审计-审计日志列表
export async function postAuditList(data: Partial<API.AuditListRequest>): Promise<API.PageResult_AuditItem_> {
  return await post<API.PageResult_AuditItem_>({
    url: `${commonUrl}/api/v1/audit/list`,
    data,
  });
}

