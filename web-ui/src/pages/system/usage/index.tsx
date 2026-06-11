// Agent Token 用量审计 (只读, 管理员): per-query token + 缓存 + 估算成本明细, 近7天/全时段切换。
// 轻量聚合: 状态 + 取数(getAgentUsage) + 表格编排; 列定义 / 数据装配下沉到 components / utils。
import { useCallback, useEffect, useMemo, useState } from 'react';

import { Segmented } from 'antd';

import PageContainer from '@/components/PageContainer';
import ResizableTable from '@/components/ResizableTable';
import { getAgentUsage } from '@/services/apis/reportsapi';

import { createColumns } from './components/Columns';
import type { UsageRow, WindowKey } from './components/types';
import { buildRows, WINDOW_OPTIONS } from './components/utils';

const UsageAuditPage: React.FC = () => {
  const [data, setData] = useState<API.AgentUsageReportResponse | undefined>(undefined);
  const [windowKey, setWindowKey] = useState<WindowKey>('last7d');
  const [loading, setLoading] = useState(false);

  const columns = useMemo(() => createColumns(), []);
  const rows = useMemo(() => buildRows(data?.[windowKey]), [data, windowKey]);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const res = await getAgentUsage();
      setData(res.data);
    } catch {
      setData(undefined);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleWindowChange = useCallback((value: WindowKey): void => {
    setWindowKey(value);
  }, []);

  const segmented = useMemo(
    () => (
      <Segmented<WindowKey>
        options={WINDOW_OPTIONS}
        value={windowKey}
        onChange={handleWindowChange}
      />
    ),
    [windowKey, handleWindowChange],
  );

  useEffect(() => {
    load();
  }, [load]);

  return (
    <PageContainer>
      <div className="mb-12 flex justify-end">{segmented}</div>
      <ResizableTable<UsageRow>
        rowKey="key"
        loading={loading}
        columns={columns}
        dataSource={rows}
        search={false}
        options={false}
        toolBarRender={false}
        pagination={{ defaultPageSize: 20 }}
        scroll={{ x: 1100 }}
      />
    </PageContainer>
  );
};

export default UsageAuditPage;
