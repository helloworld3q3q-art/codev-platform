// 统一图谱 — 全屏 3D 画布单页, 渲染 graph/store.py 内全部插件产出的完整图谱
// (前端路由/组件/接口调用 → 后端端点/函数 → 数据表/字段 全链), 让插件产出
// (尤其 sql 的 db_table/db_column) 在页面可见。复用 codegraph 的 Graph3DCanvas。
//
// kind 多选筛选: 只渲染勾选类型的节点 (边的两端都在可见集合里才保留)。
// 节点上色复用 Graph3DCanvas 内置的 codegraph 配色, 故把统一 kind 映射成
// codegraph 兼容 kind (db_table→table 等), 使颜色与本页图例一致。

import { useModel } from '@umijs/max';
import { Spin, message } from 'antd';
import React, { useCallback, useEffect, useMemo, useState } from 'react';

import Graph3DCanvas from '../codegraph/graph/components/Graph3DCanvas';
import type { NeighborsResponse, NodeDTO } from '../codegraph/common/types';
import { fetchUnifiedGraph, fetchUnifiedStats } from './common/services';
import type { UnifiedGraphResponse, UnifiedGraphStatsResponse } from './common/types';
import KindFilter from './components/KindFilter';

// 统一 kind → codegraph 兼容 kind (供 Graph3DCanvas 内置配色识别)。
// 未列出的 kind 透传 (Graph3DCanvas fallback 灰色)。
const KIND_TO_CODEGRAPH: Record<string, string> = {
  db_table: 'table',
  db_column: 'column',
  backend_endpoint: 'java_endpoint',
  backend_function: 'function',
  frontend_route: 'frontend_page',
  frontend_component: 'frontend_page',
  frontend_api_call: 'frontend_api',
};

function toCodegraphKind(kind?: string): string {
  return KIND_TO_CODEGRAPH[kind ?? ''] ?? (kind ?? 'file');
}

interface GraphState {
  graph: UnifiedGraphResponse | undefined;
  stats: UnifiedGraphStatsResponse | undefined;
}

const UnifiedGraphPage: React.FC = () => {
  // 订阅当前项目, 切项目后图谱原地重拉 (fetch 拦截器据此注入 X-Org-Id / X-Project-Id)。
  const { currentProjectId } = useModel('project');
  const [data, setData] = useState<GraphState>({ graph: undefined, stats: undefined });
  const [loading, setLoading] = useState(false);
  const [selectedKinds, setSelectedKinds] = useState<string[]>([]);

  const loadGraph = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const [graph, stats] = await Promise.all([fetchUnifiedGraph(), fetchUnifiedStats()]);
      setData({ graph, stats });
      // 默认全选所有出现过的 kind
      setSelectedKinds(Object.keys(stats?.nodesByKind ?? {}));
    } catch (err) {
      setData({ graph: undefined, stats: undefined });
      message.error(`加载统一图谱失败: ${(err as Error).message}`);
    } finally {
      setLoading(false);
    }
    // currentProjectId 变化触发重拉
  }, [currentProjectId]);

  const handleToggleKind = useCallback((kind: string): void => {
    setSelectedKinds((prev) =>
      prev.includes(kind) ? prev.filter((k) => k !== kind) : [...prev, kind],
    );
  }, []);

  const handleSelectAll = useCallback((): void => {
    setSelectedKinds(Object.keys(data.stats?.nodesByKind ?? {}));
  }, [data.stats]);

  const handleClear = useCallback((): void => {
    setSelectedKinds([]);
  }, []);

  useEffect(() => {
    loadGraph();
  }, [loadGraph]);

  // 按选中 kind 过滤节点, 再据可见节点过滤边; 适配成 Graph3DCanvas 的 NeighborsResponse 形状。
  const canvasData = useMemo<NeighborsResponse>(() => {
    const allNodes = data.graph?.nodes ?? [];
    const allEdges = data.graph?.edges ?? [];
    const selectedSet = new Set(selectedKinds);
    const visibleNodes = allNodes.filter((n) => selectedSet.has(n.kind ?? ''));
    const visibleIds = new Set(visibleNodes.map((n) => n.id ?? ''));
    const nodes: NodeDTO[] = visibleNodes.map((n) => ({
      id: n.id ?? '',
      // 映射成 codegraph 兼容 kind, 让 Graph3DCanvas 内置配色识别
      kind: toCodegraphKind(n.kind),
      name: n.name ?? '',
      filePath: n.filePath ?? undefined,
      startLine: n.startLine ?? undefined,
      language: n.language ?? undefined,
    }));
    const edges = allEdges
      .filter((e) => visibleIds.has(e.source ?? '') && visibleIds.has(e.target ?? ''))
      .map((e) => ({ source: e.source ?? '', target: e.target ?? '', kind: e.kind ?? undefined }));
    return { center: undefined, nodes, edges };
  }, [data.graph, selectedKinds]);

  const visibleCount = canvasData.nodes?.length ?? 0;
  const visibleEdges = canvasData.edges?.length ?? 0;

  return (
    <div
      className="relative w-full"
      style={{
        height: 'calc(100vh - 47px)',
        background:
          'radial-gradient(ellipse at center, #142447 0%, #0a1628 60%, #050b18 100%)',
      }}
    >
      <KindFilter
        kindCounts={data.stats?.nodesByKind ?? {}}
        selected={selectedKinds}
        onToggle={handleToggleKind}
        onSelectAll={handleSelectAll}
        onClear={handleClear}
      />

      {/* 状态卡片 — 右上角 */}
      <div
        className="absolute top-16 right-16 z-10 px-12 py-6 rounded-6 text-12 flex items-center gap-8"
        style={{
          background: 'rgba(255, 255, 255, 0.92)',
          backdropFilter: 'blur(4px)',
          border: '1px solid #f0f0f0',
          boxShadow: '0 2px 8px rgba(0, 0, 0, 0.06)',
        }}
      >
        <span className="text-#8c8c8c">统一图谱</span>
        <span className="font-600">
          {visibleCount} / {data.stats?.totalNodes ?? 0} 节点 · {visibleEdges} 边
        </span>
      </div>

      {loading ? (
        <div
          className="absolute inset-0 z-20 flex items-center justify-center"
          style={{ background: 'rgba(10, 22, 40, 0.5)', backdropFilter: 'blur(2px)' }}
        >
          <Spin size="large">
            <div className="px-24 py-16 text-#bfbfbf">加载统一图谱…</div>
          </Spin>
        </div>
      ) : null}

      <Graph3DCanvas
        data={canvasData}
        showLegend={false}
        height={window.innerHeight - 56}
      />
    </div>
  );
};

export default UnifiedGraphPage;
