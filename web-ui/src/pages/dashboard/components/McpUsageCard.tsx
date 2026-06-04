// MCP 调用分析卡 v2 —— 每项目一行 (分组列) + 合计行固定底部, 近7天 / 全时段切换。
// chroma 按 agent/dev 分桶 + agent/dev 命中; 模型 (自部署) embed/rerank 各按 agent/dev 分桶;
// cross-link / codegraph 为纯开发端调用数 (列头带 "(开发端)")。
import { useCallback, useMemo, useState } from 'react';

import { ProCard } from '@ant-design/pro-components';
import type { ProColumns } from '@ant-design/pro-components';
import { Empty, Segmented, Table as AntTable } from 'antd';

import Table from '@/components/Table';

import { type McpUsageWindowKey } from './utils';

interface McpUsageRow {
  key: string;
  projectId: string;
  chromaAgentCalls: number;
  chromaDevCalls: number;
  chromaAgentHits: number;
  chromaDevHits: number;
  crossLink: number;
  codegraph: number;
  embedAgent: number;
  embedDev: number;
  rerankAgent: number;
  rerankDev: number;
}

interface McpUsageTotals {
  chromaAgentCalls: number;
  chromaDevCalls: number;
  chromaAgentHits: number;
  chromaDevHits: number;
  crossLink: number;
  codegraph: number;
  embedAgent: number;
  embedDev: number;
  rerankAgent: number;
  rerankDev: number;
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
  },
  {
    title: 'chroma',
    children: [
      { title: 'agent 调用', dataIndex: 'chromaAgentCalls', align: 'right', width: 110 },
      { title: 'dev 调用', dataIndex: 'chromaDevCalls', align: 'right', width: 110 },
      { title: 'agent 命中', dataIndex: 'chromaAgentHits', align: 'right', width: 110 },
      { title: 'dev 命中', dataIndex: 'chromaDevHits', align: 'right', width: 110 },
    ],
  },
  {
    title: '模型(自部署)',
    children: [
      { title: 'embed·agent', dataIndex: 'embedAgent', align: 'right', width: 110 },
      { title: 'embed·dev', dataIndex: 'embedDev', align: 'right', width: 110 },
      { title: 'rerank·agent', dataIndex: 'rerankAgent', align: 'right', width: 110 },
      { title: 'rerank·dev', dataIndex: 'rerankDev', align: 'right', width: 110 },
    ],
  },
  { title: 'cross-link(开发端)', dataIndex: 'crossLink', align: 'right', width: 130 },
  { title: 'codegraph(开发端)', dataIndex: 'codegraph', align: 'right', width: 130 },
];

// 合计行各列在 summary 里的渲染顺序 (与扁平后的叶子列一一对应)。
const SUMMARY_FIELDS: (keyof McpUsageTotals)[] = [
  'chromaAgentCalls',
  'chromaDevCalls',
  'chromaAgentHits',
  'chromaDevHits',
  'embedAgent',
  'embedDev',
  'rerankAgent',
  'rerankDev',
  'crossLink',
  'codegraph',
];

function buildRows(window?: API.McpUsageWindow): McpUsageRow[] {
  if (!window) {
    return [];
  }
  return (window.projects ?? []).map((p, idx) => ({
    key: p.projectId ?? `project-${idx}`,
    projectId: p.projectId ?? '-',
    chromaAgentCalls: p.chroma?.agentCalls ?? 0,
    chromaDevCalls: p.chroma?.devCalls ?? 0,
    chromaAgentHits: p.chroma?.agentHits ?? 0,
    chromaDevHits: p.chroma?.devHits ?? 0,
    crossLink: p.crossLink?.calls ?? 0,
    codegraph: p.codegraph?.calls ?? 0,
    embedAgent: p.model?.agentEmbed ?? 0,
    embedDev: p.model?.devEmbed ?? 0,
    rerankAgent: p.model?.agentRerank ?? 0,
    rerankDev: p.model?.devRerank ?? 0,
  }));
}

function buildTotals(window?: API.McpUsageWindow): McpUsageTotals {
  const t = window?.total;
  return {
    chromaAgentCalls: t?.chroma?.agentCalls ?? 0,
    chromaDevCalls: t?.chroma?.devCalls ?? 0,
    chromaAgentHits: t?.chroma?.agentHits ?? 0,
    chromaDevHits: t?.chroma?.devHits ?? 0,
    crossLink: t?.crossLink?.calls ?? 0,
    codegraph: t?.codegraph?.calls ?? 0,
    embedAgent: t?.model?.agentEmbed ?? 0,
    embedDev: t?.model?.devEmbed ?? 0,
    rerankAgent: t?.model?.agentRerank ?? 0,
    rerankDev: t?.model?.devRerank ?? 0,
  };
}

const McpUsageCard: React.FC<McpUsageCardProps> = ({ data }) => {
  const [windowKey, setWindowKey] = useState<McpUsageWindowKey>('last7d');

  const handleWindowChange = useCallback((value: McpUsageWindowKey) => {
    setWindowKey(value);
  }, []);

  const rows = useMemo(() => buildRows(data?.[windowKey]), [data, windowKey]);
  const totals = useMemo(() => buildTotals(data?.[windowKey]), [data, windowKey]);

  const isEmpty = rows.length === 0;

  // 合计行固定在表格底部 (不入 dataSource, 避免分页冲到末页)。
  const renderSummary = useCallback(
    () => (
      <AntTable.Summary fixed>
        <AntTable.Summary.Row className="bg-#fafafa">
          <AntTable.Summary.Cell index={0}>
            <strong>合计</strong>
          </AntTable.Summary.Cell>
          {SUMMARY_FIELDS.map((field, idx) => (
            <AntTable.Summary.Cell key={field} index={idx + 1} align="right">
              <strong>{totals[field]}</strong>
            </AntTable.Summary.Cell>
          ))}
        </AntTable.Summary.Row>
      </AntTable.Summary>
    ),
    [totals],
  );

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
          pagination={{ pageSize: 10, hideOnSinglePage: true }}
          search={false}
          options={false}
          toolBarRender={false}
          scroll={{ x: 1320 }}
          summary={renderSummary}
        />
      )}
    </ProCard>
  );
};

export default McpUsageCard;
