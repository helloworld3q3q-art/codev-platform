import { Tag, Tooltip } from 'antd';
import React from 'react';

export type PositionMode = 'formula' | 'fixed' | 'kelly';

export interface PositionModeBadgeProps {
  mode: PositionMode | string | null | undefined;
  size?: 'small' | 'default';
  showTooltip?: boolean;
  // 可选追加文案前缀，例如 "当前策略："
  prefix?: string;
}

const MODE_LABEL: Record<string, string> = {
  formula: '公式',
  fixed: '档位',
  kelly: 'Kelly',
};

const MODE_COLOR: Record<string, string> = {
  formula: 'green',
  fixed: 'orange',
  kelly: 'blue',
};

const MODE_TOOLTIP: Record<string, string> = {
  formula: '5 因子动态：评分 × 置信度 × 信号强度 × 风险等级 × max 上限。生产推荐使用。',
  fixed:
    '按评分查档位（≥80 → 0.08，≥60 → 0.05，<60 → 0.03）。早期保守模式，CLOSED < 50 时 fallback。',
  kelly: 'Kelly 公式动态加权。需 ≥100 CLOSED 样本解锁，当前未启用。',
};

const PositionModeBadge: React.FC<PositionModeBadgeProps> = ({
  mode,
  size = 'default',
  showTooltip = true,
  prefix,
}) => {
  const normalized = String(mode ?? '').toLowerCase();
  const label = MODE_LABEL[normalized] ?? '未标记';
  const color = MODE_COLOR[normalized] ?? 'default';
  const tip = MODE_TOOLTIP[normalized] ?? '该数据无 mode 标签，可能是 5-15 之前的混合策略数据';

  const tag = (
    <Tag color={color} className={size === 'small' ? 'text-12' : 'text-14'}>
      {prefix}
      {label}
    </Tag>
  );

  return showTooltip ? <Tooltip title={tip}>{tag}</Tooltip> : tag;
};

export default PositionModeBadge;
