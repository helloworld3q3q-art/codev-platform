/**
 * 策略版本号 / 污染标签工具函数。
 * 含 `north-contaminated` 子串的推荐 = 基于 2026-05-15 已 deprecated 的北向数据，
 * 应在 UI 上加红色 ⚠ 警示让用户知晓。
 */

export const CONTAMINATED_TAGS = ['north-contaminated'] as const;

export function isContaminated(policyVersion?: string | null): boolean {
  if (!policyVersion) return false;
  return CONTAMINATED_TAGS.some((tag) => policyVersion.includes(tag));
}

export function getContaminationReason(policyVersion?: string | null): string | null {
  if (!policyVersion) return null;
  if (policyVersion.includes('north-contaminated')) {
    return '该推荐部分信号来自 2026-05-15 已 deprecated 的北向数据，不建议依赖';
  }
  return null;
}

// TODO: pnpm run api 重新生成 typings.d.ts 后，下面改回精确类型
export function readPolicyVersion(record: unknown): string | undefined {
  if (record && typeof record === 'object' && 'policyVersion' in record) {
    const v = (record as { policyVersion?: unknown }).policyVersion;
    return typeof v === 'string' ? v : undefined;
  }
  return undefined;
}

/**
 * 把 policy_version 翻译为用户可见的中文标签，用于推荐列表 / 胜率页等位置展示。
 * - null / undefined → "EOD 默认"（兼容历史 TRACKING 无标签数据）
 * - *north-contaminated* → 数据源已下线
 * - *shadow-contaminated* → 影子污染
 * - *-morning → 早盘版（含 margin）
 * - *-eod → EOD 预演版
 * - 其他 → 原样返回
 */
export function getPolicyVersionLabel(policyVersion?: string | null): string {
  if (!policyVersion) return 'EOD 默认';
  if (policyVersion.includes('north-contaminated')) return '⚠ 数据源已下线';
  if (policyVersion.includes('shadow-contaminated')) return '⚠ 影子污染';
  if (policyVersion.endsWith('-morning')) return '早盘版（含 margin）';
  if (policyVersion.endsWith('-eod')) return 'EOD 预演版';
  return policyVersion;
}

// 已知的两个生产 policy_version（按 2026-05-18 起规划）
// 早盘版含融资融券新规则；EOD 预演版仅做晚间复盘，不发布
export const POLICY_VERSIONS = {
  MORNING: 'p1.14-morning',
  EOD: 'p1.14-eod',
} as const;
