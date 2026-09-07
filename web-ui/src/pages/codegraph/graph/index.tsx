// CodeGraph 节点图谱 — 全屏 3D 画布单页。
// 默认初始化加载全图（postGraph，最多 2000 节点），点击节点展开邻居切换视图。
// 顶部浮层搜索 + 切换中心节点；URL ?focus=<id> 直接定位。
// 鼠标左键拖拽旋转 / 右键平移 / 滚轮缩放 / 拖节点物理重排。

import { FullscreenExitOutlined, FullscreenOutlined } from '@ant-design/icons';
import { useLocation, useModel } from '@umijs/max';
import { AutoComplete, Spin, Tag, Tooltip, message } from 'antd';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { postGraph, postNeighbors, postSearch } from '@/services/apis/graphapi';

import type { NeighborsResponse, NodeDTO } from '../common/types';
import { nodeColorOf } from '../common/utils';
import Graph3DCanvas from './components/Graph3DCanvas';

// 全图初始 limit — 18000 节点全加会卡死浏览器，2000 节点足够形成密集星云
const INITIAL_GRAPH_LIMIT = 2000;

const CodeGraphGraphPage: React.FC = () => {
  const location = useLocation();
  // 订阅当前项目, 切项目后图谱原地重拉(fetch 拦截器据此注入 X-Project-Id)。
  const { currentProjectId } = useModel('project');
  const wrapperRef = useRef<HTMLDivElement>(null);
  const [centerId, setCenterId] = useState<string | undefined>(undefined);
  const [graphData, setGraphData] = useState<NeighborsResponse | undefined>(undefined);
  const [graphLoading, setGraphLoading] = useState(false);
  const [overviewMode, setOverviewMode] = useState(true);
  const [keyword, setKeyword] = useState('');
  const [searchOpts, setSearchOpts] = useState<NodeDTO[]>([]);
  const [isFullscreen, setIsFullscreen] = useState(false);

  const handleToggleFullscreen = useCallback(async (): Promise<void> => {
    if (!wrapperRef.current) return;
    if (document.fullscreenElement) {
      await document.exitFullscreen();
    } else {
      await wrapperRef.current.requestFullscreen();
    }
  }, []);

  // 跟随浏览器原生全屏事件同步 state（用户按 Esc 退出全屏也能正确切回图标）
  useEffect(() => {
    const onChange = (): void => {
      setIsFullscreen(Boolean(document.fullscreenElement));
    };
    document.addEventListener('fullscreenchange', onChange);
    return () => document.removeEventListener('fullscreenchange', onChange);
  }, []);

  const loadOverview = useCallback(async (): Promise<void> => {
    setGraphLoading(true);
    setCenterId(undefined);
    setOverviewMode(true);
    try {
      const res = await postGraph({ limit: INITIAL_GRAPH_LIMIT });
      // 把 GraphResponse 适配成 NeighborsResponse 形状（center=undefined，Graph3DCanvas 已兼容）
      setGraphData({
        center: undefined,
        nodes: res.data?.nodes ?? [],
        edges: res.data?.edges ?? [],
      });
    } catch (err) {
      setGraphData(undefined);
      message.error(`加载全图失败: ${(err as Error).message}`);
    } finally {
      setGraphLoading(false);
    }
    // currentProjectId 变化触发重拉
  }, [currentProjectId]);

  const loadNeighbors = useCallback(async (id: string): Promise<void> => {
    setGraphLoading(true);
    setCenterId(id);
    setOverviewMode(false);
    try {
      const res = await postNeighbors({ id, direction: 'both', depth: 1 });
      setGraphData(res.data);
    } catch (err) {
      setGraphData(undefined);
      message.error(`加载邻居失败: ${(err as Error).message}`);
    } finally {
      setGraphLoading(false);
    }
    // currentProjectId 变化触发重拉
  }, [currentProjectId]);

  const handleSearchInput = useCallback(async (value: string): Promise<void> => {
    setKeyword(value);
    if (!value) {
      setSearchOpts([]);
      return;
    }
    try {
      const res = await postSearch({ keyword: value, limit: 30 });
      setSearchOpts(res.data?.items ?? []);
    } catch {
      setSearchOpts([]);
    }
  }, []);

  const handlePickSearch = useCallback(
    (id: string): void => {
      const node = searchOpts.find((n) => n.id === id);
      setKeyword(node?.name ?? '');
      loadNeighbors(id);
    },
    [loadNeighbors, searchOpts],
  );

  const handleGraphNodeClick = useCallback(
    (id: string): void => {
      loadNeighbors(id);
    },
    [loadNeighbors],
  );

  const handleBackToOverview = useCallback((): void => {
    setKeyword('');
    loadOverview();
  }, [loadOverview]);

  // 入口加载：?focus=<id> 优先，否则加载全图概览
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const focusId = params.get('focus') ?? params.get('nodeId');
    if (focusId) {
      loadNeighbors(focusId);
    } else {
      loadOverview();
    }
  }, [loadNeighbors, loadOverview, location.search]);

  const autoCompleteOptions = useMemo(
    () =>
      searchOpts.map((n) => ({
        value: n.id ?? '',
        label: (
          <div className="flex items-center gap-8">
            <span
              className="inline-block w-8 h-8 rounded-full"
              style={{ background: nodeColorOf(n.kind) }}
            />
            <span className="flex-1 truncate">{n.name}</span>
            <Tag color={nodeColorOf(n.kind)} className="i:m-0">
              {n.kind}
            </Tag>
          </div>
        ),
      })),
    [searchOpts],
  );

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
      {/* 顶部浮层搜索 */}
      <div
        className="absolute top-16 left-1/2 z-10"
        style={{ transform: 'translateX(-50%)', width: 480 }}
      >
        <AutoComplete
          value={keyword}
          options={autoCompleteOptions}
          onSearch={handleSearchInput}
          onSelect={handlePickSearch}
          placeholder="搜索类 / 方法 / 文件…"
          allowClear
          style={{
            width: '100%',
            boxShadow: '0 4px 16px rgba(0, 0, 0, 0.08)',
            borderRadius: 8,
          }}
          size="large"
        />
      </div>

      {/* 右上角全屏按钮 */}
      <Tooltip title={isFullscreen ? '退出全屏 (Esc)' : '全屏显示'}>
        <button
          type="button"
          onClick={handleToggleFullscreen}
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
      </Tooltip>

      {/* 状态卡片 — 全屏按钮左侧 */}
      <div
        className="absolute top-16 z-10 px-12 py-6 rounded-6 text-12 flex items-center gap-8"
        style={{
          right: 64,
          background: 'rgba(255, 255, 255, 0.92)',
          backdropFilter: 'blur(4px)',
          border: '1px solid #f0f0f0',
          boxShadow: '0 2px 8px rgba(0, 0, 0, 0.06)',
        }}
      >
        {overviewMode ? (
          <>
            <span className="text-#8c8c8c">全图概览</span>
            <span className="font-600">{graphData?.nodes?.length ?? 0} 节点 · {graphData?.edges?.length ?? 0} 边</span>
          </>
        ) : (
          <>
            <Tooltip title={centerId}>
              <span>
                <span className="text-#8c8c8c">中心 </span>
                <span className="font-600">
                  {centerId && centerId.length > 28 ? `${centerId.slice(0, 28)}…` : centerId}
                </span>
              </span>
            </Tooltip>
            <button
              type="button"
              className="border-none bg-transparent text-#1677ff cursor-pointer text-12"
              onClick={handleBackToOverview}
            >
              ← 返回全图
            </button>
          </>
        )}
      </div>

      {/* loading 浮层 */}
      {graphLoading ? (
        <div
          className="absolute inset-0 z-20 flex items-center justify-center"
          style={{ background: 'rgba(10, 22, 40, 0.5)', backdropFilter: 'blur(2px)' }}
        >
          <Spin size="large">
            <div className="px-24 py-16 text-#bfbfbf">
              {overviewMode ? '加载全图…' : '加载邻居关系…'}
            </div>
          </Spin>
        </div>
      ) : null}

      <Graph3DCanvas
        data={graphData}
        centerId={centerId}
        onNodeClick={handleGraphNodeClick}
        height={isFullscreen ? window.innerHeight : window.innerHeight - 56}
      />
    </div>
  );
};

export default CodeGraphGraphPage;
