// 仪表盘数据装配 —— 并发拉取平台健康 + 当前项目图谱统计 + 资源计数, 各源独立容错。
import { postOrgsList } from '@/services/apis/orgapi';
import { getCheck } from '@/services/apis/healthapi';
import { postAudit, postStats, postStats2 } from '@/services/apis/graphapi';
import { postStatus } from '@/services/apis/indexapi';
import { postProjectsList } from '@/services/apis/projectapi';
import { getMcpUsage } from '@/services/apis/reportsapi';

export interface DashboardData {
  health?: API.HealthData;
  codegraph?: API.CodegraphStatsResponse;
  // cross-link 已并入统一图谱 (血缘重构), 这里展示统一图谱 store 的 stats。
  unified?: API.UnifiedGraphStatsResponse;
  // MCP 调用分析 (仅管理员可见, 后端 403 兜底); 非管理员不拉取。
  mcpUsage?: API.McpUsageReportResponse;
  // 各类索引相对当前 HEAD 的新鲜度 (统一 IndexManifest, Phase 1)。
  indexStatus?: API.IndexStatusResponse;
  // 统一图谱结构审计摘要 (Phase 3)。
  graphAudit?: API.GraphAuditResponse;
  projectCount: number;
  orgCount: number;
}

export const DASHBOARD_DEFAULT: DashboardData = {
  health: undefined,
  codegraph: undefined,
  unified: undefined,
  mcpUsage: undefined,
  indexStatus: undefined,
  graphAudit: undefined,
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

// MCP 调用分析窗口口径: last7d / allTime。getMcpUsage 失败或未授权 (403) 时返回 undefined, 卡内置空 graceful。
export type McpUsageWindowKey = 'last7d' | 'allTime';

async function loadMcpUsage(enabled: boolean): Promise<API.McpUsageReportResponse | undefined> {
  if (!enabled) {
    return undefined;
  }
  return safe(async (): Promise<API.McpUsageReportResponse | undefined> => {
    const res = await getMcpUsage();
    return res.data as API.McpUsageReportResponse | undefined;
  }, undefined);
}

export async function loadDashboard(isAdmin: boolean): Promise<DashboardData> {
  const [health, codegraph, unified, projectCount, orgCount, mcpUsage, indexStatus, graphAudit] =
    await Promise.all([
      safe(async (): Promise<API.HealthData | undefined> => {
        const res = await getCheck();
        return res.data;
      }, undefined),
      safe(async (): Promise<API.CodegraphStatsResponse | undefined> => {
        const res = await postStats();
        return res.data;
      }, undefined),
      safe(async (): Promise<API.UnifiedGraphStatsResponse | undefined> => {
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
      loadMcpUsage(isAdmin),
      safe(async (): Promise<API.IndexStatusResponse | undefined> => {
        const res = await postStatus();
        return res.data;
      }, undefined),
      safe(async (): Promise<API.GraphAuditResponse | undefined> => {
        const res = await postAudit();
        return res.data;
      }, undefined),
    ]);
  return {
    health,
    codegraph,
    unified,
    projectCount,
    orgCount,
    mcpUsage,
    indexStatus,
    graphAudit,
  };
}
