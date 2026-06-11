// Token 用量审计页类型。
export type WindowKey = 'last7d' | 'allTime';

export interface UsageRow extends API.AgentUsageEntry {
  key: string;
}
