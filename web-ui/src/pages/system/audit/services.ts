// 审计日志数据层: 把生成的 postAuditList 包成 fetchAuditList + 搜索参数转换。
import { postAuditList } from '@/services/apis/auditapi';

// ProTable 搜索 + 分页参数 → 后端 AuditListRequest。
// ResizableTable 已把 current 映射为 pageNum; dateTimeRange 列以 [from, to] 数组传入。
export const convertParams = (
  params: Record<string, unknown>,
): Partial<API.AuditListRequest> & { pageNumber: number; pageSize: number } => {
  const range = params.tsRange as [string, string] | undefined;
  // allowed 下拉值是字符串 'true'/'false', 后端 AuditListRequest.allowed 是 boolean —
  // 这里转成真布尔, 否则后端按 bool 过滤永不命中 (空/未选 → undefined 不过滤)。
  const allowedRaw = params.allowed as string | undefined;
  return {
    pageNumber: (params.pageNum as number) ?? 1,
    pageSize: (params.pageSize as number) ?? 20,
    service: (params.service as string) || undefined,
    userId: (params.userId as string) || undefined,
    projectId: (params.projectId as string) || undefined,
    allowed: allowedRaw === undefined || allowedRaw === '' ? undefined : allowedRaw === 'true',
    tsFrom: range?.[0] || undefined,
    tsTo: range?.[1] || undefined,
  };
};

// 列表请求 (传给 requestWrapper 的 apiFunction)。
export const fetchAuditList = (
  data: Partial<API.AuditListRequest>,
): Promise<API.PageResult_AuditItem_> => {
  return postAuditList(data);
};
