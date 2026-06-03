// 统一图谱 — 全屏 3D 画布单页, 渲染 graph/store.py 内全部插件产出的完整图谱
// (前端路由/组件/接口调用 → 后端端点/函数 → 数据表/字段 全链), 让插件产出
// (尤其 sql 的 db_table/db_column) 在页面可见。复用 codegraph 的 Graph3DCanvas。
//
// kind 多选筛选: 只渲染勾选类型的节点 (边的两端都在可见集合里才保留)。
// 节点上色/大小/标签走统一图谱自己的语言中性映射 (unifiedNodeColorOf 等),
// 注入给 Graph3DCanvas, 不再把 backend_endpoint 伪装成 java_endpoint。
// 点击节点弹右侧详情面板 (NodeDetailPanel)。

import { useModel } from '@umijs/max';
import { Spin, message } from 'antd';
import React, { useCallback, useEffect, useMemo, useState } from 'react';

import { postGraph2, postStats2 } from '@/services/apis/graphapi';

import Graph3DCanvas from '../codegraph/graph/components/Graph3DCanvas';
import type { NeighborsResponse, NodeDTO } from '../codegraph/common/types';
import type {
  UnifiedGraphNode,
  UnifiedGraphResponse,
  UnifiedGraphStatsResponse,
} from './common/types';
import { unifiedKindLabelOf, unifiedNodeColorOf, unifiedNodeSizeOf } from './common/utils';
import KindFilter from './components/KindFilter';
import NodeDetailPanel from './components/NodeDetailPanel';

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
  const [selectedNode, setSelectedNode] = useState<UnifiedGraphNode | undefined>(undefined);

  const loadGraph = useCallback(async (): Promise<void> => {
    setLoading(true);
    setSelectedNode(undefined);
    try {
      // 直接调生成的 API (postGraph2=/unified/graph, postStats2=/unified/stats), 自取 .data, 不套适配层。
      const [graphRes, statsRes] = await Promise.all([postGraph2(), postStats2()]);
      const graph = graphRes.data;
      const stats = statsRes.data;
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

  // 整层切换: 该层全选则取消整层, 否则补全整层 (用于快速看某层 / 拼跨层链路视图)。
  const handleToggleLayer = useCallback((layerKinds: string[]): void => {
    setSelectedKinds((prev) => {
      const set = new Set(prev);
      const allOn = layerKinds.every((k) => set.has(k));
      layerKinds.forEach((k) => {
        if (allOn) {
          set.delete(k);
        } else {
          set.add(k);
        }
      });
      return Array.from(set);
    });
  }, []);

  // id → 原始统一节点, 供点击后查详情 (Graph3DCanvas 的 onNodeClick 只回传 id)。
  const nodeById = useMemo(() => {
    const map = new Map<string, UnifiedGraphNode>();
    for (const n of data.graph?.nodes ?? []) {
      if (n.id) map.set(n.id, n);
    }
    return map;
  }, [data.graph]);

  const handleNodeClick = useCallback(
    (id: string): void => {
      setSelectedNode(nodeById.get(id));
    },
    [nodeById],
  );

  const handleCloseDetail = useCallback((): void => {
    setSelectedNode(undefined);
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
      // 保留原始统一 kind, 颜色/大小/标签由注入的 unified* 函数处理
      kind: n.kind ?? '',
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
        onToggleLayer={handleToggleLayer}
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

      {selectedNode ? (
        <NodeDetailPanel
          node={selectedNode}
          nodes={data.graph?.nodes ?? []}
          edges={data.graph?.edges ?? []}
          onJumpTo={handleNodeClick}
          onClose={handleCloseDetail}
        />
      ) : null}

      <Graph3DCanvas
        data={canvasData}
        showLegend={false}
        height={window.innerHeight - 56}
        onNodeClick={handleNodeClick}
        nodeColorFn={unifiedNodeColorOf}
        nodeSizeFn={unifiedNodeSizeOf}
        kindLabelFn={unifiedKindLabelOf}
      />
    </div>
  );
};

export default UnifiedGraphPage;
