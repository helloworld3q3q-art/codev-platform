// Token 用量审计页数据装配 —— recent 明细 → 表格行(加稳定 key)。
import type { UsageRow, WindowKey } from './types';

export const WINDOW_OPTIONS: { label: string; value: WindowKey }[] = [
  { label: '近7天', value: 'last7d' },
  { label: '全时段', value: 'allTime' },
];

export function buildRows(w?: API.AgentUsageWindow): UsageRow[] {
  return (w?.recent ?? []).map((e, idx): UsageRow => {
    return { ...e, key: `${e.ts ?? 0}-${idx}` };
  });
}
