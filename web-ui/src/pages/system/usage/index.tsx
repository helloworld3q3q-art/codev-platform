// Agent Token 用量审计 (只读, 管理员): per-query token + 缓存 + 估算成本明细, 近7天/全时段切换。
// 数据来自 getAgentUsage().recent (聚合端点读 agent_trace), 客户端表格 (recent 上限 50 条)。
import { useCallback, useEffect, useMemo, useState } from 'react';

import type { ProColumns } from '@ant-design/pro-components';
import { Segmented } from 'antd';
import dayjs from 'dayjs';

import PageContainer from '@/components/PageContainer';
import Table from '@/components/Table';
import { getAgentUsage } from '@/services/apis/reportsapi';

type WindowKey = 'last7d' | 'allTime';

interface UsageRow extends API.AgentUsageEntry {
  key: string;
}

const WINDOW_OPTIONS: { label: string; value: WindowKey }[] = [
  { label: '近7天', value: 'last7d' },
  { label: '全时段', value: 'allTime' },
];

function renderTime(_: unknown, r: UsageRow): string {
  return r.ts ? dayjs.unix(r.ts).format('YYYY/MM/DD HH:mm:ss') : '—';
}

function renderCost(_: unknown, r: UsageRow): string {
  return `$${(r.costUsd ?? 0).toFixed(6)}`;
}

function renderHit(_: unknown, r: UsageRow): string {
  const ci = (r.cacheHitTokens ?? 0) + (r.cacheMissTokens ?? 0);
  if (!ci) {
    return '—';
  }
  return `${(((r.cacheHitTokens ?? 0) / ci) * 100).toFixed(0)}%`;
}

const COLUMNS: ProColumns<UsageRow>[] = [
  { title: '时间', dataIndex: 'ts', width: 170, render: renderTime },
  { title: '会话', dataIndex: 'sessionId', width: 200, ellipsis: true },
  { title: '模型', dataIndex: 'model', width: 160 },
  { title: '步数', dataIndex: 'steps', align: 'right', width: 70 },
  { title: '收尾', dataIndex: 'stopReason', width: 100 },
  { title: 'input', dataIndex: 'inputTokens', align: 'right', width: 100 },
  { title: 'output', dataIndex: 'outputTokens', align: 'right', width: 90 },
  { title: '缓存命中', dataIndex: 'cacheHitTokens', align: 'right', width: 90, render: renderHit },
  { title: '成本($)', dataIndex: 'costUsd', align: 'right', width: 120, render: renderCost },
];

function buildRows(w?: API.AgentUsageWindow): UsageRow[] {
  return (w?.recent ?? []).map((e, idx) => ({ ...e, key: `${e.ts ?? 0}-${idx}` }));
}

const UsageAuditPage: React.FC = () => {
  const [data, setData] = useState<API.AgentUsageReportResponse | undefined>(undefined);
  const [windowKey, setWindowKey] = useState<WindowKey>('last7d');
  const [loading, setLoading] = useState(false);

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

  const rows = useMemo(() => buildRows(data?.[windowKey]), [data, windowKey]);

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
      <Table<UsageRow>
        rowKey="key"
        loading={loading}
        columns={COLUMNS}
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
