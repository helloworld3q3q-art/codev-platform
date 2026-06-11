// Agent Token 用量卡 —— 总量(查询/token/缓存率/估算成本) + 按模型, 近7天/全时段切换。读 agent_trace 聚合。
import { useCallback, useMemo, useState } from 'react';

import type { ProColumns } from '@ant-design/pro-components';
import { ProCard, StatisticCard } from '@ant-design/pro-components';
import { Empty, Segmented } from 'antd';

import Table from '@/components/Table';

type WindowKey = 'last7d' | 'allTime';

interface TokenUsageCardProps {
  data?: API.AgentUsageReportResponse;
}

interface ModelRow {
  key: string;
  model: string;
  queries: number;
  inputTokens: number;
  outputTokens: number;
  cacheHitRate: number;
  costUsd: number;
}

const WINDOW_OPTIONS: { label: string; value: WindowKey }[] = [
  { label: '近7天', value: 'last7d' },
  { label: '全时段', value: 'allTime' },
];

function renderRate(_: unknown, r: ModelRow): string {
  return `${(r.cacheHitRate * 100).toFixed(1)}%`;
}

function renderCost(_: unknown, r: ModelRow): string {
  return `$${r.costUsd.toFixed(4)}`;
}

const COLUMNS: ProColumns<ModelRow>[] = [
  { title: '模型', dataIndex: 'model', width: 180 },
  { title: '查询数', dataIndex: 'queries', align: 'right', width: 90 },
  { title: 'input', dataIndex: 'inputTokens', align: 'right', width: 120 },
  { title: 'output', dataIndex: 'outputTokens', align: 'right', width: 110 },
  {
    title: '缓存命中率',
    dataIndex: 'cacheHitRate',
    align: 'right',
    width: 110,
    render: renderRate,
  },
  { title: '估算成本($)', dataIndex: 'costUsd', align: 'right', width: 120, render: renderCost },
];

function buildRows(w?: API.AgentUsageWindow): ModelRow[] {
  return (w?.byModel ?? []).map((m, idx) => ({
    key: m.model ?? `m-${idx}`,
    model: m.model ?? '-',
    queries: m.queries ?? 0,
    inputTokens: m.inputTokens ?? 0,
    outputTokens: m.outputTokens ?? 0,
    cacheHitRate: m.cacheHitRate ?? 0,
    costUsd: m.costUsd ?? 0,
  }));
}

const TokenUsageCard: React.FC<TokenUsageCardProps> = ({ data }) => {
  const [windowKey, setWindowKey] = useState<WindowKey>('last7d');

  const handleWindowChange = useCallback((value: WindowKey): void => {
    setWindowKey(value);
  }, []);

  const w = useMemo(() => data?.[windowKey], [data, windowKey]);
  const rows = useMemo(() => buildRows(w), [w]);
  const hitRate = w?.cacheHitRate ?? 0;

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

  return (
    <ProCard
      title="Agent Token 用量"
      variant="outlined"
      classNames={{ root: 'i:mb-16' }}
      extra={segmented}
    >
      <StatisticCard.Group direction="row" className="i:mb-12">
        <StatisticCard statistic={{ title: '查询数', value: w?.queries ?? 0 }} />
        <StatisticCard statistic={{ title: 'input tokens', value: w?.inputTokens ?? 0 }} />
        <StatisticCard statistic={{ title: 'output tokens', value: w?.outputTokens ?? 0 }} />
        <StatisticCard
          statistic={{ title: '缓存命中率', value: `${(hitRate * 100).toFixed(1)}%` }}
        />
        <StatisticCard
          statistic={{ title: '估算成本', value: `$${(w?.costUsd ?? 0).toFixed(4)}` }}
        />
      </StatisticCard.Group>
      {rows.length ? (
        <Table<ModelRow>
          rowKey="key"
          columns={COLUMNS}
          dataSource={rows}
          pagination={false}
          search={false}
          options={false}
          toolBarRender={false}
          scroll={{ x: 730 }}
        />
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无 agent 用量数据" />
      )}
    </ProCard>
  );
};

export default TokenUsageCard;
