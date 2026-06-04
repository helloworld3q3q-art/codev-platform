// MCP 调用分析卡 —— 每项目一行 + 合计行, 近7天 / 全时段切换。
// chroma 按 agent/dev 分桶 + 命中数; cross-link / codegraph 调用数; 自部署模型 embed/rerank 调用数。
import { useCallback, useMemo, useState } from 'react';

import { ProCard } from '@ant-design/pro-components';
import type { ProColumns } from '@ant-design/pro-components';
import { Empty, Segmented } from 'antd';

import Table from '@/components/Table';

import { type McpUsageWindowKey } from './utils';

interface McpUsageRow {
  key: string;
  projectId: string;
  chromaAgent: number;
  chromaDev: number;
  chromaHits: number;
  crossLink: number;
  codegraph: number;
  embed: number;
  rerank: number;
  isTotal?: boolean;
}

interface McpUsageCardProps {
  data?: API.McpUsageReportResponse;
}

const WINDOW_OPTIONS: { label: string; value: McpUsageWindowKey }[] = [
  { label: '近7天', value: 'last7d' },
  { label: '全时段', value: 'allTime' },
];

const COLUMNS: ProColumns<McpUsageRow>[] = [
  {
    title: '项目',
    dataIndex: 'projectId',
    fixed: 'left',
    width: 160,
    render: (_, record) => (record.isTotal ? <strong>{record.projectId}</strong> : record.projectId),
  },
  { title: 'chroma · agent', dataIndex: 'chromaAgent', align: 'right', width: 120 },
  { title: 'chroma · dev', dataIndex: 'chromaDev', align: 'right', width: 120 },
  { title: 'chroma · 命中', dataIndex: 'chromaHits', align: 'right', width: 120 },
  { title: 'cross-link', dataIndex: 'crossLink', align: 'right', width: 110 },
  { title: 'codegraph', dataIndex: 'codegraph', align: 'right', width: 110 },
  { title: '模型 · embed', dataIndex: 'embed', align: 'right', width: 120 },
  { title: '模型 · rerank', dataIndex: 'rerank', align: 'right', width: 120 },
];

function buildRows(window?: API.McpUsageWindow): McpUsageRow[] {
  if (!window) {
    return [];
  }
  const projectRows: McpUsageRow[] = (window.projects ?? []).map((p, idx) => ({
    key: p.projectId ?? `project-${idx}`,
    projectId: p.projectId ?? '-',
    chromaAgent: p.chroma?.agentCalls ?? 0,
    chromaDev: p.chroma?.devCalls ?? 0,
    chromaHits: p.chroma?.hits ?? 0,
    crossLink: p.crossLink?.calls ?? 0,
    codegraph: p.codegraph?.calls ?? 0,
    embed: p.model?.embedCalls ?? 0,
    rerank: p.model?.rerankCalls ?? 0,
  }));

  const t = window.total;
  const totalRow: McpUsageRow = {
    key: '__total__',
    projectId: '合计',
    chromaAgent: t?.chroma?.agentCalls ?? 0,
    chromaDev: t?.chroma?.devCalls ?? 0,
    chromaHits: t?.chroma?.hits ?? 0,
    crossLink: t?.crossLink?.calls ?? 0,
    codegraph: t?.codegraph?.calls ?? 0,
    embed: t?.model?.embedCalls ?? 0,
    rerank: t?.model?.rerankCalls ?? 0,
    isTotal: true,
  };
  return [...projectRows, totalRow];
}

const McpUsageCard: React.FC<McpUsageCardProps> = ({ data }) => {
  const [windowKey, setWindowKey] = useState<McpUsageWindowKey>('last7d');

  const handleWindowChange = useCallback((value: McpUsageWindowKey) => {
    setWindowKey(value);
  }, []);

  const rowClassName = useCallback(
    (record: McpUsageRow) => (record.isTotal ? 'bg-#fafafa' : ''),
    [],
  );

  const rows = useMemo(() => buildRows(data?.[windowKey]), [data, windowKey]);

  // 只有合计行 (无项目数据) 视为空。
  const isEmpty = rows.length === 0 || rows.every((r) => r.isTotal);

  const segmented = useMemo(
    () => (
      <Segmented<McpUsageWindowKey>
        options={WINDOW_OPTIONS}
        value={windowKey}
        onChange={handleWindowChange}
      />
    ),
    [windowKey, handleWindowChange],
  );

  return (
    <ProCard title="MCP 调用分析" variant="outlined" classNames={{ root: 'i:mb-16' }} extra={segmented}>
      {isEmpty ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无 MCP 调用数据" />
      ) : (
        <Table<McpUsageRow>
          rowKey="key"
          columns={COLUMNS}
          dataSource={rows}
          pagination={false}
          search={false}
          options={false}
          toolBarRender={false}
          scroll={{ x: 1080 }}
          rowClassName={rowClassName}
        />
      )}
    </ProCard>
  );
};

export default McpUsageCard;
