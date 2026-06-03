// 影响分析页数据归一化 + 展示常量。
// 把 reports API 4 种响应 (impact / tableUsage / pageDependencies / apiCallers)
// 收敛成统一的 ResultView (目标节点 + 按层分组清单 + 风险 + summary), 供 index/ResultPanel 渲染。
// 后端壳: impact 走 ImpactReportResponse; 其余 3 个走 GraphQueryResponse.data 内的引擎原始 dict。

export type QueryKind = 'impact' | 'tableUsage' | 'pageDependencies' | 'apiCallers';

export interface ImpactNodeItem {
  id: string;
  kind: string;
  name: string;
  layer: string;
  file?: string;
  line?: number;
  depth?: number;
  viaEdge?: string;
}

export interface ResultView {
  found: boolean;
  targetName?: string;
  targetKind?: string;
  targetLayer?: string;
  risk?: string;
  summary?: string;
  byLayer: Record<string, ImpactNodeItem[]>;
  total: number;
  ambiguous: ImpactNodeItem[];
  emptyHint?: string;
}

// 查询类型切换项 (纯前端 UI 开关, 决定调哪个 API, 不进入请求体)。
export const QUERY_OPTIONS: { label: string; value: QueryKind }[] = [
  { label: '改动影响', value: 'impact' },
  { label: '表被谁用', value: 'tableUsage' },
  { label: '页依赖什么', value: 'pageDependencies' },
  { label: '端点被谁调', value: 'apiCallers' },
];

export const QUERY_META: Record<QueryKind, { placeholder: string; hint: string }> = {
  impact: {
    placeholder: '输入节点 id 或名称 (函数 / 端点 / 表名)',
    hint: '反向追溯: 改动该节点会跨层波及哪些前端 / 后端 / 数据节点',
  },
  tableUsage: {
    placeholder: '输入数据表名, 如 stock_quote_daily',
    hint: '哪些后端函数 / 端点 / 前端在用这张表',
  },
  pageDependencies: {
    placeholder: '输入前端路由 / 组件 / API 调用的 id',
    hint: '正向展开: 这个前端页依赖的端点 / 函数 / 表',
  },
  apiCallers: {
    placeholder: '输入后端端点 id 或名称',
    hint: '哪些前端在调用这个端点',
  },
};

export const LAYER_ORDER = ['frontend', 'backend', 'database', 'other'];

export const LAYER_LABEL: Record<string, string> = {
  frontend: '前端层',
  backend: '后端层',
  database: '数据层',
  other: '其他',
};

// antd Tag 预设色名 (非硬编码 hex)。
export const LAYER_COLOR: Record<string, string> = {
  frontend: 'geekblue',
  backend: 'purple',
  database: 'cyan',
  other: 'default',
};

export const RISK_COLOR: Record<string, string> = {
  high: 'red',
  medium: 'orange',
  low: 'green',
};

export const RISK_LABEL: Record<string, string> = {
  high: '高风险',
  medium: '中风险',
  low: '低风险',
};

type RawNode = Record<string, unknown>;

const str = (v: unknown): string => {
  if (v === null || v === undefined) {
    return '';
  }
  return String(v);
};

const numOrUndef = (v: unknown): number | undefined => {
  if (typeof v === 'number') {
    return v;
  }
  return undefined;
};

const asObj = (v: unknown): RawNode => {
  if (v && typeof v === 'object') {
    return v as RawNode;
  }
  return {};
};

const toItem = (raw: RawNode): ImpactNodeItem => {
  return {
    id: str(raw.id),
    kind: str(raw.kind),
    name: str(raw.name),
    layer: str(raw.layer) || 'other',
    file: raw.file !== null && raw.file !== undefined ? str(raw.file) : undefined,
    line: numOrUndef(raw.line),
    depth: numOrUndef(raw.depth),
    viaEdge: raw.via_edge !== null && raw.via_edge !== undefined ? str(raw.via_edge) : undefined,
  };
};

const toItems = (v: unknown): ImpactNodeItem[] => {
  if (!Array.isArray(v)) {
    return [];
  }
  return v.map((n) => toItem(asObj(n)));
};

// 引擎 _grouped() 产出的 byLayer ({frontend:[...], backend:[...]}) → 归一化分组。
const groupFrom = (byLayer: unknown): Record<string, ImpactNodeItem[]> => {
  const src = asObj(byLayer);
  const out: Record<string, ImpactNodeItem[]> = {};
  for (const key of Object.keys(src)) {
    const items = toItems(src[key]);
    if (items.length > 0) {
      out[key] = items;
    }
  }
  return out;
};

const countAll = (byLayer: Record<string, ImpactNodeItem[]>): number => {
  let total = 0;
  for (const key of Object.keys(byLayer)) {
    total += byLayer[key].length;
  }
  return total;
};

// impact: 后端已展开成 ImpactReportResponse (target / impact.byLayer / risk / summary / ambiguous)。
export const normalizeImpact = (resp?: API.ImpactReportResponse): ResultView => {
  if (!resp || !resp.found) {
    const ambiguous = toItems(resp?.ambiguous);
    return {
      found: false,
      byLayer: {},
      total: 0,
      ambiguous,
      emptyHint: resp?.summary || '未找到节点或该项目尚无统一图谱索引',
    };
  }
  const target = asObj(resp.target);
  const impact = asObj(resp.impact);
  return {
    found: true,
    targetName: str(target.name),
    targetKind: str(target.kind),
    targetLayer: str(target.layer),
    risk: resp.risk ? str(resp.risk) : undefined,
    summary: resp.summary ? str(resp.summary) : undefined,
    byLayer: groupFrom(impact.byLayer),
    total: numOrUndef(resp.total) ?? numOrUndef(impact.total) ?? 0,
    ambiguous: [],
  };
};

// tableUsage / pageDependencies / apiCallers: 引擎原始 dict 包在 GraphQueryResponse.data 里。
export const normalizeQuery = (kind: QueryKind, resp?: API.GraphQueryResponse): ResultView => {
  const data = asObj(resp?.data);
  const found = Boolean(resp?.found && data.found !== false);
  if (!found) {
    const ambiguous = toItems(data.ambiguous);
    return {
      found: false,
      byLayer: {},
      total: 0,
      ambiguous,
      emptyHint: ambiguous.length > 0
        ? '名称有多个同名候选, 请改用 id 精确查询'
        : '未找到或该项目尚无统一图谱索引',
    };
  }

  if (kind === 'apiCallers') {
    const target = asObj(data.endpoint);
    const callers = toItems(data.callers);
    return {
      found: true,
      targetName: str(target.name),
      targetKind: str(target.kind),
      targetLayer: str(target.layer),
      byLayer: callers.length > 0 ? { frontend: callers } : {},
      total: callers.length,
      ambiguous: [],
    };
  }

  const target = kind === 'tableUsage' ? asObj(data.table) : asObj(data.page);
  const group = kind === 'tableUsage' ? asObj(data.usage).byLayer : asObj(data.dependsOn).byLayer;
  const byLayer = groupFrom(group);
  return {
    found: true,
    targetName: str(target.name),
    targetKind: str(target.kind),
    targetLayer: str(target.layer),
    byLayer,
    total: countAll(byLayer),
    ambiguous: [],
  };
};

// summary 是 markdown 风格 (含 ** 加粗标记), 展示前去掉 ** 保留换行。
export const cleanSummary = (summary: string): string => {
  return summary.split('**').join('');
};
