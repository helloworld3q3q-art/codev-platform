// 仪表盘数据装配 —— 并发拉取平台健康 + 当前项目图谱统计 + 资源计数, 各源独立容错。
import { postOrgsList } from '@/services/apis/orgapi';
import { getCheck } from '@/services/apis/healthapi';
import { postStats, postStats2 } from '@/services/apis/graphapi';
import { postProjectsList } from '@/services/apis/projectapi';

export interface DashboardData {
  health?: API.HealthData;
  codegraph?: API.CodegraphStatsResponse;
  crosslink?: API.CrossLinkStatsResponse;
  projectCount: number;
  orgCount: number;
}

export const DASHBOARD_DEFAULT: DashboardData = {
  health: undefined,
  codegraph: undefined,
  crosslink: undefined,
  projectCount: 0,
  orgCount: 0,
};

// 单源容错: 任一接口失败 (后端未起 / 未选项目 / 索引缺失) 不拖垮整个仪表盘。
async function safe<T>(fn: () => Promise<T>, fallback: T): Promise<T> {
  try {
    return await fn();
  } catch {
    return fallback;
  }
}

export async function loadDashboard(): Promise<DashboardData> {
  const [health, codegraph, crosslink, projectCount, orgCount] = await Promise.all([
    safe(async (): Promise<API.HealthData | undefined> => {
      const res = await getCheck();
      return res.data;
    }, undefined),
    safe(async (): Promise<API.CodegraphStatsResponse | undefined> => {
      const res = await postStats();
      return res.data;
    }, undefined),
    safe(async (): Promise<API.CrossLinkStatsResponse | undefined> => {
      const res = await postStats2();
      return res.data;
    }, undefined),
    safe(async (): Promise<number> => {
      const res = await postProjectsList({ pageNumber: 1, pageSize: 1 });
      return res.total ?? 0;
    }, 0),
    safe(async (): Promise<number> => {
      const res = await postOrgsList({ pageNumber: 1, pageSize: 1 });
      return res.total ?? 0;
    }, 0),
  ]);
  return { health, codegraph, crosslink, projectCount, orgCount };
}
