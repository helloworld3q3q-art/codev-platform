/**
 * 涨跌颜色全局统一工具（中国 A 股惯例：红涨绿跌）。
 *
 * 使用语义：
 * - priceColor: 涨跌语义（正红 / 负绿 / 零灰），用于收益率、PnL、价格变动、归因贡献、止盈/止损方向等
 * - healthColor: 健康度语义（高绿 / 低红 / 中黄），用于命中率、IC、数据完整度、覆盖率等"高=好"指标
 * - riskColor: 风险提示恒红色，用于回撤、最大亏损、告警数等任何数值都意味着风险的场景
 * - STOCK_UP_COLOR / STOCK_DOWN_COLOR: ECharts / Plotly 色条直接使用的硬编码常量
 *
 * 关键：healthColor 与 priceColor 反向！
 *  - priceColor：高=红涨好、低=绿跌坏（按 A 股 K 线）
 *  - healthColor：高=绿健康、低=红告警（按交通灯通用规则）
 */

/** A 股红涨色（涨幅/盈利/止盈触发方向） */
export const STOCK_UP_COLOR = '#cf1322';

/** A 股绿跌色（跌幅/亏损/止损触发方向） */
export const STOCK_DOWN_COLOR = '#389e0d';

/** 中性灰（无值或零） */
export const STOCK_FLAT_COLOR = '#8c8c8c';

/** 健康度黄色（介于 good / bad 阈值之间） */
export const STOCK_WARN_COLOR = '#faad14';

/**
 * 涨跌语义颜色：正红 / 负绿 / 零灰。
 * 适用：收益率、PnL、价格变动、归因贡献、止盈/止损方向、资金净流入。
 */
export const priceColor = (value: number | null | undefined): string => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return STOCK_FLAT_COLOR;
  }
  const v = Number(value);
  if (v > 0) return STOCK_UP_COLOR;
  if (v < 0) return STOCK_DOWN_COLOR;
  return STOCK_FLAT_COLOR;
};

/**
 * 健康度语义颜色：高=绿（健康）/ 低=红（告警）/ 中=黄。
 * 适用：命中率、数据完整度、覆盖率、样本进度等"高=好"指标。
 *
 * 默认阈值按 0-100 比例尺；若数值是 0-1 小数（如 IC、胜率小数形式），调用方需自行换算或显式传阈值。
 *
 * @param value 当前值
 * @param goodThreshold 达标阈值（>= 即绿，默认 60）
 * @param badThreshold 警戒阈值（< 即红，默认 30）
 */
export const healthColor = (
  value: number | null | undefined,
  goodThreshold = 60,
  badThreshold = 30,
): string => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return STOCK_FLAT_COLOR;
  }
  const v = Number(value);
  if (v >= goodThreshold) return STOCK_DOWN_COLOR;
  if (v < badThreshold) return STOCK_UP_COLOR;
  return STOCK_WARN_COLOR;
};

/**
 * 风险提示恒红色（任何数值都视为风险，仅用于"出现即提示"的场景）。
 * 适用：告警数 > 0、超阈回撤、止损触发标记等。
 */
export const riskColor = (): string => STOCK_UP_COLOR;
