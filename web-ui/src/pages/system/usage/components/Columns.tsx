// Token 用量审计列定义 —— 时间/会话/模型/步数/收尾/token/缓存命中/成本。
import type { ProColumns } from '@ant-design/pro-components';
import dayjs from 'dayjs';

import type { UsageRow } from '../types';

function renderTime(_: unknown, r: UsageRow): string {
  return r.ts ? dayjs.unix(r.ts).format('YYYY/MM/DD HH:mm:ss') : '—';
}

function renderHit(_: unknown, r: UsageRow): string {
  const ci = (r.cacheHitTokens ?? 0) + (r.cacheMissTokens ?? 0);
  if (!ci) {
    return '—';
  }
  return `${(((r.cacheHitTokens ?? 0) / ci) * 100).toFixed(0)}%`;
}

function renderCost(_: unknown, r: UsageRow): string {
  return `$${(r.costUsd ?? 0).toFixed(6)}`;
}

export function createColumns(): ProColumns<UsageRow>[] {
  return [
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
}
