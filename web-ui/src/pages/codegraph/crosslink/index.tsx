import {
  DownOutlined,
  FullscreenExitOutlined,
  FullscreenOutlined,
  UpOutlined,
} from '@ant-design/icons';
import { Select, Spin, message } from 'antd';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { fetchCrossLinkGraph, fetchCrossLinkTables } from '../common/services';
import type {
  CrossLinkGraphRequest,
  CrossLinkGraphResponse,
  EdgeDTO,
  NeighborsResponse,
  NodeDTO,
} from '../common/types';
import { nodeColorOf } from '../common/utils';
import Graph3DCanvas from '../graph/components/Graph3DCanvas';
import NodeDetailPanel from './components/NodeDetailPanel';

type GraphMode = 'overview' | 'full' | 'kind';

interface GraphFilter {
  key: string;
  label: string;
  mode: GraphMode;
  kinds?: string[];
  limit?: number;
  primary?: boolean;
}

const GRAPH_FILTERS: GraphFilter[] = [
  { key: 'overview', label: '跨层总览', mode: 'overview', limit: 3000, primary: true },
  { key: 'frontend_page', label: '前端页面', mode: 'kind', kinds: ['frontend_page'], limit: 5000, primary: true },
  { key: 'frontend_api', label: '前端接口', mode: 'kind', kinds: ['frontend_api'], limit: 5000, primary: true },
  { key: 'java_endpoint', label: 'Java 接口', mode: 'kind', kinds: ['java_endpoint'], limit: 5000, primary: true },
  { key: 'table', label: '数据表', mode: 'kind', kinds: ['table'], limit: 5000, primary: true },
  { key: 'java_method', label: 'Java 方法', mode: 'kind', kinds: ['java_method'], limit: 3000 },
  { key: 'python_method', label: 'Python 方法', mode: 'kind', kinds: ['python_method'], limit: 5000 },
  { key: 'flyway_migration', label: 'Flyway 迁移', mode: 'kind', kinds: ['flyway_migration'], limit: 5000 },
  { key: 'column', label: '字段', mode: 'kind', kinds: ['column'], limit: 5000 },
  { key: 'full', label: '完整图', mode: 'full', limit: 3000 },
];

interface GraphFilterButtonProps {
  filter: GraphFilter;
  active: boolean;
  count?: number;
  onClick: (filter: GraphFilter) => void;
}

const GraphFilterButton: React.FC<GraphFilterButtonProps> = ({
  filter,
  active,
  count,
  onClick,
}) => {
  const handleClick = useCallback((): void => {
    onClick(filter);
  }, [filter, onClick]);

  const color = filter.kinds?.length === 1 ? nodeColorOf(filter.kinds[0]) : '#1677ff';

  return (
    <button
      type="button"
      onClick={handleClick}
      className="cursor-pointer border-none rounded-6 px-10 py-6 text-12 flex items-center gap-6"
      style={{
        background: active ? '#1677ff' : 'rgba(255,255,255,0.92)',
        color: active ? '#fff' : '#262626',
        boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
      }}
    >
      <span
        className="inline-block rounded-full"
        style={{
          width: 8,
          height: 8,
          background: active ? '#fff' : color,
        }}
      />
      <span>{filter.label}</span>
      {count !== undefined ? (
        <span style={{ color: active ? 'rgba(255,255,255,0.8)' : '#8c8c8c' }}>
          {count}
        </span>
      ) : null}
    </button>
  );
};

function toNeighborsResponse(g: CrossLinkGraphResponse | undefined): NeighborsResponse {
  if (!g) return { center: undefined, nodes: [], edges: [] };
  const nodes: NodeDTO[] = (g.nodes ?? []).map((n) => ({
    id: n.id,
    kind: n.kind,
    name: n.name,
    filePath: n.filePath,
    startLine: n.startLine,
    language: n.language,
  }));
  const edges: EdgeDTO[] = (g.edges ?? []).map((e) => ({
    source: e.source,
    target: e.target,
    kind: e.kind,
  }));
  return { center: undefined, nodes, edges };
}

const CrossLinkPage: React.FC = () => {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const [tables, setTables] = useState<string[]>([]);
  const [centerTable, setCenterTable] = useState<string | undefined>(undefined);
  const [graphData, setGraphData] = useState<NeighborsResponse | undefined>(undefined);
  const [graphLoading, setGraphLoading] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [legendExpanded, setLegendExpanded] = useState(false);
  const [selectedNodeId, setSelectedNodeId] = useState<string | undefined>(undefined);
  const [activeFilterKey, setActiveFilterKey] = useState('overview');

  const activeFilter = useMemo(
    () => GRAPH_FILTERS.find((f) => f.key === activeFilterKey) ?? GRAPH_FILTERS[0],
    [activeFilterKey],
  );

  const visibleFilters = useMemo(() => {
    if (legendExpanded) return GRAPH_FILTERS;
    return [activeFilter];
  }, [activeFilter, legendExpanded]);

  const hiddenFilterCount = GRAPH_FILTERS.length - visibleFilters.length;

  const handleToggleLegend = useCallback((): void => {
    setLegendExpanded((prev) => !prev);
  }, []);

  const handleToggleFullscreen = useCallback(async (): Promise<void> => {
    if (!wrapperRef.current) return;
    if (document.fullscreenElement) {
      await document.exitFullscreen();
    } else {
      await wrapperRef.current.requestFullscreen();
    }
  }, []);

  const loadTables = useCallback(async (): Promise<void> => {
    try {
      const res = await fetchCrossLinkTables();
      setTables(res?.tables ?? []);
    } catch {
      setTables([]);
    }
  }, []);

  const loadGraph = useCallback(async (filter: GraphFilter): Promise<void> => {
    setGraphLoading(true);
    try {
      const query: Partial<CrossLinkGraphRequest> = {
        mode: filter.mode,
        kinds: filter.kinds,
        limit: filter.limit,
      };
      const res = await fetchCrossLinkGraph(query);
      setGraphData(toNeighborsResponse(res));
      setSelectedNodeId(undefined);
    } catch (err) {
      setGraphData({ center: undefined, nodes: [], edges: [] });
      message.error(`加载图谱失败：${(err as Error).message}`);
    } finally {
      setGraphLoading(false);
    }
  }, []);

  const handleFilterClick = useCallback((filter: GraphFilter): void => {
    setActiveFilterKey(filter.key);
    setCenterTable(undefined);
    void loadGraph(filter);
  }, [loadGraph]);

  const handleTableChange = useCallback((value: string | undefined): void => {
    setCenterTable(value);
    setSelectedNodeId(undefined);
  }, []);

  const handleGraphNodeClick = useCallback((id: string): void => {
    setSelectedNodeId(id);
    if (id.startsWith('table:')) {
      const tableName = graphData?.nodes?.find((n) => n.id === id)?.name;
      if (tableName) setCenterTable(tableName);
    }
  }, [graphData]);

  const handleJumpToNeighbor = useCallback((id: string): void => {
    setSelectedNodeId(id);
    if (id.startsWith('table:')) {
      const tableName = graphData?.nodes?.find((n) => n.id === id)?.name;
      if (tableName) setCenterTable(tableName);
    } else {
      setCenterTable(undefined);
    }
  }, [graphData]);

  const handleClosePanel = useCallback((): void => {
    setSelectedNodeId(undefined);
  }, []);

  useEffect(() => {
    const onChange = (): void => {
      setIsFullscreen(Boolean(document.fullscreenElement));
    };
    document.addEventListener('fullscreenchange', onChange);
    return () => document.removeEventListener('fullscreenchange', onChange);
  }, []);

  useEffect(() => {
    loadTables();
    void loadGraph(GRAPH_FILTERS[0]);
  }, [loadTables, loadGraph]);

  const tableOptions = useMemo(
    () => tables.map((t) => ({ value: t, label: t })),
    [tables],
  );

  const filterOption = useCallback(
    (input: string, option?: { label?: string }) =>
      String(option?.label ?? '').toLowerCase().includes(input.toLowerCase()),
    [],
  );

  const centerId = useMemo(() => {
    if (selectedNodeId) return selectedNodeId;
    if (!centerTable || !graphData?.nodes) return undefined;
    const n = graphData.nodes.find(
      (x) => x.kind === 'table' && x.name === centerTable,
    );
    return n?.id;
  }, [selectedNodeId, centerTable, graphData]);

  const kindCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const node of graphData?.nodes ?? []) {
      const kind = node.kind ?? 'unknown';
      counts.set(kind, (counts.get(kind) ?? 0) + 1);
    }
    return counts;
  }, [graphData]);

  return (
    <div
      ref={wrapperRef}
      className="relative w-full"
      style={{
        height: isFullscreen ? '100vh' : 'calc(100vh - 47px)',
        background:
          'radial-gradient(ellipse at center, #142447 0%, #0a1628 60%, #050b18 100%)',
      }}
    >
      <div
        className="absolute top-16 left-16 right-72 z-10 flex flex-wrap items-center gap-8"
        style={{ pointerEvents: 'auto' }}
      >
        {visibleFilters.map((filter) => (
          <GraphFilterButton
            key={filter.key}
            filter={filter}
            active={filter.key === activeFilterKey}
            count={filter.kinds?.length === 1 ? kindCounts.get(filter.kinds[0]) : undefined}
            onClick={handleFilterClick}
          />
        ))}
        <button
          type="button"
          onClick={handleToggleLegend}
          className="cursor-pointer border-none rounded-6 px-10 py-6 text-12 flex items-center gap-6"
          style={{
            background: 'rgba(255,255,255,0.92)',
            color: '#262626',
            boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
          }}
        >
          {legendExpanded ? <UpOutlined /> : <DownOutlined />}
          <span>{legendExpanded ? '收起' : `展开${hiddenFilterCount > 0 ? ` ${hiddenFilterCount}` : ''}`}</span>
        </button>
      </div>

      <div className="absolute top-68 left-16 z-10" style={{ width: 460 }}>
        <Select
          value={centerTable}
          options={tableOptions}
          onChange={handleTableChange}
          placeholder="选择数据表并高亮中心节点"
          allowClear
          showSearch={{ filterOption }}
          size="large"
          style={{
            width: '100%',
            boxShadow: '0 4px 16px rgba(0, 0, 0, 0.08)',
            borderRadius: 8,
          }}
        />
      </div>

      <button
        type="button"
        onClick={handleToggleFullscreen}
        title={isFullscreen ? '退出全屏（Esc）' : '全屏显示'}
        className="absolute top-16 right-16 z-10 cursor-pointer flex items-center justify-center"
        style={{
          width: 36,
          height: 36,
          background: 'rgba(255, 255, 255, 0.92)',
          backdropFilter: 'blur(4px)',
          border: '1px solid #f0f0f0',
          boxShadow: '0 2px 8px rgba(0, 0, 0, 0.06)',
          borderRadius: 6,
          color: '#1677ff',
          fontSize: 16,
        }}
      >
        {isFullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
      </button>

      <div
        className="absolute top-68 right-16 z-10 px-12 py-6 rounded-6 text-12 flex items-center gap-8"
        style={{
          background: 'rgba(255, 255, 255, 0.92)',
          backdropFilter: 'blur(4px)',
          border: '1px solid #f0f0f0',
          boxShadow: '0 2px 8px rgba(0, 0, 0, 0.06)',
        }}
      >
        <span className="font-600">{activeFilter.label}</span>
        <span className="text-#8c8c8c">·</span>
        <span className="font-600">{graphData?.nodes?.length ?? 0}</span>
        <span className="text-#8c8c8c">节点</span>
        <span className="text-#8c8c8c">·</span>
        <span className="font-600">{graphData?.edges?.length ?? 0}</span>
        <span className="text-#8c8c8c">关系</span>
        {centerTable ? (
          <>
            <span className="text-#8c8c8c">·</span>
            <span className="text-#8c8c8c">中心</span>
            <span className="font-600">{centerTable}</span>
          </>
        ) : null}
      </div>

      {graphLoading ? (
        <div
          className="absolute inset-0 z-20 flex items-center justify-center"
          style={{ background: 'rgba(10, 22, 40, 0.5)', backdropFilter: 'blur(2px)' }}
        >
          <Spin size="large">
            <div className="px-24 py-16 text-#bfbfbf">加载跨层知识图谱...</div>
          </Spin>
        </div>
      ) : null}

      <Graph3DCanvas
        data={graphData}
        centerId={centerId}
        onNodeClick={handleGraphNodeClick}
        showLegend={false}
      />

      {selectedNodeId && graphData ? (
        <NodeDetailPanel
          nodeId={selectedNodeId}
          nodes={graphData.nodes ?? []}
          edges={graphData.edges ?? []}
          onJumpTo={handleJumpToNeighbor}
          onClose={handleClosePanel}
        />
      ) : null}
    </div>
  );
};

export default CrossLinkPage;
