// A 股整手对齐工具
// 背景：A 股最小交易单位 100 股（1 手）。仅靠"预算 × 仓位比"得到的金额不能直接展示给用户，
// 必须按单股价格向下取整到整手，否则会出现"1920 元买不起美的 1 手 / 单价 78 元"的展示笑话。

export const A_SHARE_LOT_SIZE = 100;

/**
 * A 股整手对齐：根据预算金额 + 单股价格 → (手数, 实际可下单金额)
 * 预算 / 单价 非法或 ≤ 0、资金不足以买 1 手时返回 (0, 0)
 */
export const alignToLot = (
  budgetAmount: number,
  pricePerShare: number,
  lotSize: number = A_SHARE_LOT_SIZE,
): { lots: number; alignedAmount: number } => {
  if (
    !Number.isFinite(budgetAmount) ||
    !Number.isFinite(pricePerShare) ||
    pricePerShare <= 0 ||
    budgetAmount <= 0
  ) {
    return { lots: 0, alignedAmount: 0 };
  }
  const lotAmount = pricePerShare * lotSize;
  const lots = Math.floor(budgetAmount / lotAmount);
  return { lots, alignedAmount: lots * lotAmount };
};
