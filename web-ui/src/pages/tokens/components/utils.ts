// 接入令牌行类型 + 纯 UI 派生 (无 service 包装层: 组件直调生成的 postIssue/postTokensList/postRevoke)。
import dayjs from 'dayjs';

// 列表行类型 (对齐后端 TokenItem; 不含明文, 只有 hash 前缀)。
export type TokenRow = API.TokenItem;

// 状态 Badge 颜色 (纯 UI 派生, 非业务枚举)。
export const STATUS_BADGE: Record<string, 'success' | 'default' | 'error'> = {
  ACTIVE: 'success',
  REVOKED: 'error',
};

// 状态文案 (token status 非后端业务枚举, 本地映射; ACTIVE/REVOKED 两态)。
export const STATUS_TEXT: Record<string, string> = {
  ACTIVE: '有效',
  REVOKED: '已吊销',
};

// projects ACL → 人读文案。'*'=全部项目; 数组=逗号拼接; 空=无项目权(安全默认)。
export const formatProjects = (projects: unknown): string => {
  if (projects === '*') {
    return '全部项目';
  }
  if (Array.isArray(projects)) {
    return projects.length ? projects.join(', ') : '无项目权';
  }
  return '无项目权';
};

// expiresAt (epoch 秒) → 人读到期时间。空 / 非法 = 永久。
export const formatExpiry = (expiresAt: unknown): string => {
  if (expiresAt === null || expiresAt === undefined || expiresAt === '') {
    return '永久';
  }
  const sec = Number(expiresAt);
  if (!Number.isFinite(sec)) {
    return '永久';
  }
  return dayjs(sec * 1000).format('YYYY/MM/DD HH:mm');
};
