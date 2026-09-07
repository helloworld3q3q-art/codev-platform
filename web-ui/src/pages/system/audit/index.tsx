// 访问审计日志页 (只读表格, 管理员)。搜索项 service/userId/projectId/allowed/时间范围 → postAuditList 分页。
import PageContainer from '@/components/PageContainer';
import ResizableTable, { requestWrapper } from '@/components/ResizableTable';
import { useCallback, useMemo } from 'react';

import { createColumns } from './components/Columns';
import { convertParams, fetchAuditList } from './services';
import type { AuditRow } from './types';

const AuditPage: React.FC = () => {
  const columns = useMemo(() => createColumns(), []);

  const handleTableRequest = useCallback(
    async (params: Record<string, any>, sort: any, filter: any) =>
      requestWrapper(params, sort, filter, fetchAuditList, convertParams),
    [],
  );

  // 审计记录无唯一 id, ts 同毫秒会撞 key —— 用多字段组合做稳定 rowKey。
  const getRowKey = useCallback(
    (r: AuditRow) => `${r.ts}|${r.service}|${r.userId}|${r.via}|${r.projectId}|${r.reason}`,
    [],
  );

  return (
    <PageContainer>
      <ResizableTable<AuditRow>
        rowKey={getRowKey}
        columns={columns}
        request={handleTableRequest}
        scroll={{ x: 1200 }}
        pagination={{ defaultPageSize: 20 }}
        search={{ span: 6, layout: 'vertical', defaultCollapsed: true }}
        form={{ layout: 'vertical', colon: false }}
      />
    </PageContainer>
  );
};

export default AuditPage;
