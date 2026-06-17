// 3D 力导向图谱 — react-force-graph-3d（基于 three.js）。
// 鼠标左键拖拽 = 旋转视角；右键拖拽 = 平移；滚轮 = 缩放；拖节点 = 物理重排。
// hover 节点时暂停自动旋转，避免眩晕；离开 hover 恢复。

import { DownOutlined, UpOutlined } from '@ant-design/icons';
import { Empty } from 'antd';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ForceGraph3D from 'react-force-graph-3d';
import * as THREE from 'three';

import styles from '../../common/style.less';
import type { EdgeDTO, NeighborsResponse, NodeDTO } from '../../common/types';
import { LANG_COLOR, edgeColorOf, nodeColorOf, nodeSizeOf } from '../../common/utils';
import GraphInstancedLayer from './GraphInstancedLayer';
import { buildNodeLabelSprite, resolveRenderProfile } from './utils';

interface Graph3DCanvasProps {
  data?: NeighborsResponse;
  centerId?: string;
  onNodeClick?: (id: string) => void;
  showLegend?: boolean;
  // height/width 不传时由 ResizeObserver 自动测量父容器铺满 (推荐用法)
  height?: number;
  width?: number;
  // kind → 颜色 / 大小 / 显示标签 注入口 (统一图谱用语言中性 kind, 不传则用 codegraph 配色)。
  // 不传时默认 codegraph 的 nodeColorOf / nodeSizeOf, 标签直接显示原始 kind (既有行为不变)。
  nodeColorFn?: (kind?: string) => string;
  nodeSizeFn?: (kind?: string) => number;
  kindLabelFn?: (kind?: string) => string;
  // 软边判定 (按 edge.kind)。返回 true 的边淡色 + 细线画 —— 区分"理解层标注边"(plays_role/
  // belongs_to_domain)与硬依赖边, 避免经角色/域 hub 的连通被误读成功能链路。统一图谱注入,
  // 不传则全按硬边 (codegraph 页行为不变)。
  linkIsSoftFn?: (kind?: string) => boolean;
  // 层级聚类注入口 (按 kind 给层偏移, 如 数据库=-1/后端=0/前端=1; undefined=不聚类, 自由力导)。
  // 传了则每层聚成一个 3D 球团沿 X 有序排开; 不传则正常力导云 (codegraph 页行为不变)。
  nodeClusterFn?: (kind?: string) => number | undefined;
}

// 默认按原始 kind 显示 (codegraph 页保持 [class] / [java_endpoint] 原样)。
function defaultKindLabel(kind?: string): string {
  return kind ?? '';
}

// 默认无软边 (codegraph 页不区分)。模块级常量避免每次渲染新建。
function defaultIsSoft(): boolean {
  return false;
}

// 软边显示色: 暗蓝灰, 在深底上低对比 —— 视觉退到背景, 与硬依赖边拉开。
const SOFT_LINK_COLOR = '#34425e';

// react-force-graph 要求 nodes/links 平坦字段；把 NodeDTO/EdgeDTO 透传 + 注入颜色/大小。
// x/y/z 由 react-force-graph 物理仿真运行时回写到同一对象（声明为可选以便读取）。
interface FGNode extends NodeDTO {
  color: string;
  val: number;
  x?: number;
  y?: number;
  z?: number;
  // 聚类力运行时字段: vx/vy/vz 是 d3-force 写的速度; __ax 是本节点所属层的 X 锚点 (initialize 预算)。
  vx?: number;
  vy?: number;
  vz?: number;
  __ax?: number;
}

// 层级聚类(点吸引, 非轴约束): 每层一个 3D 锚点, 沿 X 轴排开 (数据库 → 后端 → 前端)。
// 节点被拉向所属层锚点 + charge 排斥 = 每层一个 3D 球团 (不是压平的饼), 球团内表-字段小雪花自然保留。
// 旋钮: SPACING 相邻层锚点的 X 间距 (越大球团越分开, 大层需调大防重叠); STRENGTH 吸引强度 (越大球团越紧实)。
const CLUSTER_SPACING = 450;
const CLUSTER_STRENGTH = 0.12;

interface FGLink {
  source: string;
  target: string;
  kind?: string;
  color: string;
  particles: number;
  isSoft: boolean;
}

// react-force-graph-3d 的 ref / 各 accessor prop 泛型与本地平坦节点形状不严格兼容，
// 统一用该逃逸类型在 JSX 上断言（仅用于桥接第三方泛型，不扩散到业务逻辑）。
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type GraphAccessor = any;

const LEGEND_ITEMS: Array<{ label: string; color: string }> = [
  { label: 'class / interface', color: '#1677ff' },
  { label: 'method / function', color: '#52c41a' },
  { label: 'field / variable', color: '#fa8c16' },
  { label: 'HTTP route', color: '#eb2f96' },
  { label: 'import', color: '#722ed1' },
  { label: 'enum', color: '#13c2c2' },
  { label: 'file', color: '#8c8c8c' },
];

const Legend: React.FC = () => {
  const [collapsed, setCollapsed] = useState(false);
  const handleToggle = useCallback((): void => {
    setCollapsed((prev) => !prev);
  }, []);

  return (
    <div
      className={styles.graph3dLegend}
      style={{ pointerEvents: 'auto', padding: '6px 8px', lineHeight: 1.4 }}
    >
      <button
        type="button"
        onClick={handleToggle}
        title={collapsed ? '展开图例' : '收起图例'}
        className="absolute cursor-pointer border-none bg-transparent text-#8c8c8c"
        style={{ top: 4, right: 4, padding: 2, lineHeight: 1, fontSize: 10 }}
      >
        {collapsed ? <DownOutlined /> : <UpOutlined />}
      </button>
      {collapsed ? (
        // 收起态：仅一行彩色圆点，高度降到 ~22px
        <div className="flex items-center gap-6 pr-16">
          {LEGEND_ITEMS.map((it) => (
            <span
              key={it.label}
              title={it.label}
              className={styles.graph3dLegendDot}
              style={{ background: it.color, margin: 0 }}
            />
          ))}
        </div>
      ) : (
        // 展开态：2 列网格
        <div className="grid grid-cols-2 gap-x-12 gap-y-2 text-11 pr-12">
          {LEGEND_ITEMS.map((it) => (
            <div key={it.label} className="whitespace-nowrap">
              <span className={styles.graph3dLegendDot} style={{ background: it.color }} />
              <span>{it.label}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

const Graph3DCanvas: React.FC<Graph3DCanvasProps> = ({
  data,
  centerId,
  onNodeClick,
  showLegend = true,
  height: heightProp,
  width: widthProp,
  nodeColorFn = nodeColorOf,
  nodeSizeFn = nodeSizeOf,
  kindLabelFn = defaultKindLabel,
  linkIsSoftFn = defaultIsSoft,
  nodeClusterFn,
}) => {
  // 用 unknown 收口 — react-force-graph-3d 的 ref 类型未导出（forwardRef 实例），
  // 通过 ref.current.controls() 拿 OrbitControls 实例
  const fgRef = useRef<unknown>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  // instanced 模式的批量渲染层 (大图接管节点/边/标签渲染 + 拾取); mesh 模式为 null。
  const layerRef = useRef<GraphInstancedLayer | null>(null);
  // ForceGraph3D 不传 width/height 时默认用 window 尺寸 (不是父容器),
  // 必须手动 ResizeObserver 父容器再灌进去,three.js renderer 才会同步 resize。
  const [measured, setMeasured] = useState<{ w: number; h: number } | undefined>(undefined);

  useEffect(() => {
    if (!wrapperRef.current) return undefined;
    const el = wrapperRef.current;
    // 首帧同步测,避免 ForceGraph3D 用 window 默认值闪一下
    const rect = el.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) {
      setMeasured({ w: rect.width, h: rect.height });
    }
    const ro = new ResizeObserver(([entry]) => {
      if (!entry) return;
      const { width: w, height: h } = entry.contentRect;
      if (w < 1 || h < 1) return;
      setMeasured((prev) =>
        prev && Math.abs(prev.w - w) < 1 && Math.abs(prev.h - h) < 1 ? prev : { w, h },
      );
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const width = widthProp ?? measured?.w;
  const height = heightProp ?? measured?.h;

  const graphData = useMemo<{ nodes: FGNode[]; links: FGLink[] }>(() => {
    if (!data) return { nodes: [], links: [] };
    // codegraph-api 把 center 放在 data.center 单独字段，不在 data.nodes 里。
    // 必须把 center 也 push 到 nodes，否则 react-force-graph 找不到边的端点会丢弃所有边。
    const merged: NodeDTO[] = [];
    const seen = new Set<string>();
    if (data.center?.id) {
      merged.push(data.center);
      seen.add(data.center.id);
    }
    for (const n of data.nodes ?? []) {
      if (n.id && !seen.has(n.id)) {
        merged.push(n);
        seen.add(n.id);
      }
    }
    const nodes: FGNode[] = merged.map((n) => ({
      ...n,
      id: n.id ?? '',
      color: nodeColorFn(n.kind),
      // center 节点稍大一些突出显示
      val: n.id === data.center?.id ? nodeSizeFn(n.kind) * 2 : nodeSizeFn(n.kind),
    }));
    // 过滤掉端点不在 nodes 里的边，避免 react-force-graph 抛错
    const links: FGLink[] = (data.edges ?? [])
      .filter((e) => e.source && e.target && seen.has(e.source) && seen.has(e.target))
      .map((e: EdgeDTO) => {
        const isSoft = linkIsSoftFn(e.kind);
        return {
          source: e.source ?? '',
          target: e.target ?? '',
          kind: e.kind,
          // 软边淡色退背景, 硬边按 kind 本色
          color: isSoft ? SOFT_LINK_COLOR : edgeColorOf(e.kind),
          particles: e.kind === 'calls' ? 2 : 0,
          isSoft,
        };
      });
    return { nodes, links };
  }, [data, nodeColorFn, nodeSizeFn, linkIsSoftFn]);

  // 渲染档位: 按节点规模选 mesh / instanced (规模→策略集中在纯函数, 不在各处散 if)。
  const profile = useMemo(() => resolveRenderProfile(graphData.nodes.length), [graphData.nodes.length]);
  const isInstanced = profile.mode === 'instanced';

  // 飞行到指定节点（onClick 直接传入带 x/y/z 的 node 对象,最可靠）。lookAt 同时成为 Orbit 的旋转中心,
  // 即点节点会把旋转中心落到该节点上。
  const flyToNode = useCallback(
    (node: { x?: number; y?: number; z?: number }): void => {
      const ref = fgRef.current as
        | {
            cameraPosition?: (
              pos: { x: number; y: number; z: number },
              target: { x: number; y: number; z: number },
              ms: number,
            ) => void;
          }
        | null;
      if (!ref?.cameraPosition) return;
      if (node.x === undefined || node.y === undefined) return;
      const z = node.z ?? 0;
      const distance = 160;
      const distRatio = 1 + distance / Math.hypot(node.x, node.y, z || 1);
      ref.cameraPosition(
        { x: node.x * distRatio, y: node.y * distRatio, z: z * distRatio },
        { x: node.x, y: node.y, z },
        900,
      );
    },
    [],
  );

  // 节点点击：先飞过去,再回传 id
  const handleNodeClickInternal = useCallback(
    (node: { id?: string; x?: number; y?: number; z?: number }): void => {
      if (node?.x !== undefined) flyToNode(node);
      if (node?.id && onNodeClick) onNodeClick(node.id);
    },
    [onNodeClick, flyToNode],
  );

  // instanced 模式: 批量层 raycast 命中后只回传 id, 这里查回原节点对象再走统一的点击逻辑 (飞行 + 回传)。
  const handleInstancedClick = useCallback(
    (id: string): void => {
      const node = graphData.nodes.find((n) => n.id === id);
      if (node) handleNodeClickInternal(node);
    },
    [graphData, handleNodeClickInternal],
  );

  // instanced 模式: 每个仿真 tick 把节点坐标刷进批量层 (实例矩阵 + 线段端点)。
  const handleEngineTick = useCallback((): void => {
    layerRef.current?.syncPositions();
  }, []);

  // instanced 模式手动取景: 用节点真实坐标算包围盒, 把相机摆到能框住整图的位置 (替代失效的 zoomToFit)。
  const fitCameraToNodes = useCallback((): void => {
    const ref = fgRef.current as
      | {
          camera?: () => { fov?: number };
          cameraPosition?: (
            pos: { x: number; y: number; z: number },
            target: { x: number; y: number; z: number },
            ms: number,
          ) => void;
        }
      | null;
    const nodes = graphData.nodes;
    if (!ref?.cameraPosition || nodes.length === 0) return;
    let minX = Infinity, minY = Infinity, minZ = Infinity;
    let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
    for (const n of nodes) {
      if (n.x === undefined || n.y === undefined) continue;
      const z = n.z ?? 0;
      minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x);
      minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y);
      minZ = Math.min(minZ, z); maxZ = Math.max(maxZ, z);
    }
    if (minX === Infinity) return;
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2, cz = (minZ + maxZ) / 2;
    const extent = Math.max(maxX - minX, maxY - minY, maxZ - minZ, 1);
    const fov = ((ref.camera?.()?.fov ?? 50) * Math.PI) / 180;
    // 把 extent 半高放进视锥 + 1.3 余量
    const distance = (extent * 0.5) / Math.tan(fov * 0.5) * 1.3;
    ref.cameraPosition({ x: cx, y: cy, z: cz + distance }, { x: cx, y: cy, z: cz }, 800);
  }, [graphData]);

  // instanced 模式收敛时: 刷最终坐标 + 手动取景一次 (此刻布局已稳, 框得准)。
  const handleEngineStop = useCallback((): void => {
    layerRef.current?.syncPositions();
    fitCameraToNodes();
  }, [fitCameraToNodes]);

  // instanced 模式给 ForceGraph 设的可见性 accessor: 恒 false, 让它不产任何逐节点/逐边对象 (渲染交批量层)。
  const hiddenAccessor = useCallback((): boolean => false, []);

  // 节点 label：name + kind + file (kind 显示走注入的 kindLabelFn, 统一图谱显示中文标签)
  const handleNodeLabel = useCallback(
    (node: unknown): string => {
      const n = node as NodeDTO;
      const file = n.filePath ? `<br/><span style="color:#bfbfbf">${n.filePath}</span>` : '';
      return `<div style="padding:4px 8px;background:rgba(255,255,255,0.95);color:#000;border-radius:4px;font-size:12px">
      <b>${n.name ?? ''}</b> <span style="color:${nodeColorFn(n.kind)}">[${kindLabelFn(n.kind)}]</span>${file}
    </div>`;
    },
    [nodeColorFn, kindLabelFn],
  );

  // 链接 label
  const handleLinkLabel = useCallback((link: unknown): string => {
    const l = link as { kind?: string };
    return l.kind ?? '';
  }, []);

  // 各类 accessor — JSX 内联会被 react/jsx-no-bind 拦截，提到 useCallback
  const accessNodeColor = useCallback((n: { color?: string }): string => n.color ?? '#bfbfbf', []);
  const accessNodeVal = useCallback((n: { val?: number }): number => n.val ?? 4, []);

  // 生成每个节点的文字标签 Sprite — 复用共享构建器 (buildNodeLabelSprite), 与 instanced 模式同源。
  // mesh 模式逐节点挂 (nodeThreeObjectExtend=true 与默认球体共存); 无名字时返回空 Object3D 兜底。
  const buildNodeSprite = useCallback((node: unknown): object => {
    const n = node as { name?: string; color?: string };
    return buildNodeLabelSprite(n.name, n.color) ?? new THREE.Object3D();
  }, []);
  const accessLinkColor = useCallback(
    (l: { color?: string }): string => l.color ?? '#d9d9d9',
    [],
  );
  const accessLinkParticles = useCallback(
    (l: { particles?: number }): number => l.particles ?? 0,
    [],
  );
  // 软边细线 (0.3), 硬边正常 (1) —— 与淡色配合, 软边在图上退到次要。
  const accessLinkWidth = useCallback((l: { isSoft?: boolean }): number => {
    return l.isSoft ? 0.3 : 1;
  }, []);

  // 数据更新后等一帧取景。mesh 用 ForceGraph 的 zoomToFit; instanced 用手动取景 —— zoomToFit 靠
  // 场景里带 geometry 的节点对象算包围盒, instanced 下 nodeVisibility=false 无节点对象会失效
  // (相机错位致节点跑出屏幕)。这里给 instanced 一次早期取景 (部分坐标), 收敛时 handleEngineStop 再精确取景。
  useEffect(() => {
    const ref = fgRef.current as
      | { cameraPosition?: (pos: { x: number; y: number; z: number }, target?: unknown, ms?: number) => void }
      | null;
    if (!ref?.cameraPosition || !data || !data.nodes || data.nodes.length === 0) return undefined;
    const timer = window.setTimeout(() => {
      if (isInstanced) {
        fitCameraToNodes();
        return;
      }
      const r = fgRef.current as {
        zoomToFit?: (ms: number, padding: number) => void;
      } | null;
      r?.zoomToFit?.(800, 80);
    }, 600);
    return () => window.clearTimeout(timer);
  }, [data, isInstanced, fitCameraToNodes]);

  // centerId 变化 → 相机飞到该节点。
  // 关键: 直接用本组件 useMemo 的 graphData.nodes 找节点 — react-force-graph 会 mutate 同一对象
  // 把 x/y/z 写回去(不需要去 ref.graphData() 取);带重试机制应对节点位置仿真未就绪。
  useEffect(() => {
    if (!centerId || graphData.nodes.length === 0) return undefined;
    let attempts = 0;
    let timer: number | undefined;
    const tryFly = (): void => {
      attempts += 1;
      const node = graphData.nodes.find((n) => n.id === centerId);
      if (node && node.x !== undefined && node.y !== undefined) {
        flyToNode(node);
        return;
      }
      if (attempts < 20) {
        timer = window.setTimeout(tryFly, 250);
      }
    };
    timer = window.setTimeout(tryFly, 100);
    return () => {
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [centerId, graphData, flyToNode]);

  // instanced 模式生命周期: 大图时创建批量渲染层接管节点/边/标签渲染 + 拾取; 数据或档位变化时重建。
  // mesh 模式 (小图) 不创建, 走 ForceGraph 默认渲染 (零回归)。fgRef 在子组件挂载后即就绪。
  useEffect(() => {
    if (!isInstanced || graphData.nodes.length === 0) return undefined;
    const ref = fgRef.current as
      | {
          scene?: () => unknown;
          camera?: () => unknown;
          renderer?: () => unknown;
        }
      | null;
    if (!ref?.scene || !ref.camera || !ref.renderer) return undefined;
    const layer = new GraphInstancedLayer(
      ref.scene(),
      ref.camera(),
      ref.renderer(),
      graphData.nodes,
      graphData.links,
      {
        nodeResolution: profile.nodeResolution,
        labelBudget: profile.labelBudget,
        onNodeClick: handleInstancedClick,
      },
    );
    layerRef.current = layer;
    return () => {
      layer.dispose();
      layerRef.current = null;
    };
  }, [isInstanced, graphData, profile, handleInstancedClick]);

  // 层级聚类力: 注入了 nodeClusterFn 时, 给 d3 仿真挂自定义力 —— 把每个节点拉向它所属层的 3D 锚点
  // (锚点沿 X 轴按层偏移排开)。点吸引而非轴约束 → 每层聚成 3D 球团 (非压平的饼), 球内小雪花自然保留。
  // 不注入则不挂力 (codegraph 页保持原样)。
  useEffect(() => {
    if (!nodeClusterFn) return undefined;
    const ref = fgRef.current as { d3Force?: (name: string, force: unknown) => void } | null;
    if (!ref?.d3Force) return undefined;
    let simNodes: FGNode[] = [];
    const force = (alpha: number): void => {
      const k = CLUSTER_STRENGTH * alpha;
      for (const n of simNodes) {
        if (n.__ax === undefined) continue;
        // 朝锚点 (__ax, 0, 0) 的三轴弹簧 → 每层收成球团; X 错开使层有序, Y/Z 收束保持球形不摊平。
        n.vx = (n.vx ?? 0) + (n.__ax - (n.x ?? 0)) * k;
        n.vy = (n.vy ?? 0) - (n.y ?? 0) * k;
        n.vz = (n.vz ?? 0) - (n.z ?? 0) * k;
      }
    };
    (force as { initialize?: (nodes: FGNode[]) => void }).initialize = (nodes): void => {
      simNodes = nodes;
      for (const n of simNodes) {
        const layer = nodeClusterFn(n.kind);
        n.__ax = layer !== undefined ? layer * CLUSTER_SPACING : undefined;
      }
    };
    // 不调 d3ReheatSimulation (会重启动画循环, HMR 过渡态易崩); 挂载/数据变更时仿真本就在跑, 下一 tick 生效。
    try {
      ref.d3Force('layer', force);
    } catch {
      return undefined;
    }
    return () => {
      const r = fgRef.current as { d3Force?: (name: string, force: unknown) => void } | null;
      try {
        r?.d3Force?.('layer', null);
      } catch {
        // 实例已拆, 无需清理
      }
    };
  }, [graphData, nodeClusterFn]);

  if (!data || !data.nodes || data.nodes.length === 0) {
    return (
      <div
        ref={wrapperRef}
        className="flex items-center justify-center bg-#fafafa rounded-6"
        style={{
          height: heightProp ?? '100%',
          width: widthProp ?? '100%',
        }}
      >
        <Empty description="左侧搜索后点击结果展开邻居" />
      </div>
    );
  }

  // 通过函数将 fgRef 转交 — react-force-graph-3d 的 ref 形态用 callback ref 更稳
  return (
    <div
      ref={wrapperRef}
      className={styles.graph3dWrapper}
      style={{
        height: heightProp ?? '100%',
        width: widthProp ?? '100%',
      }}
    >
      <ForceGraph3D
        // react-force-graph-3d 的 ref / accessor 泛型对自定义平坦节点形状过严，
        // 用 GraphAccessor 局部放行（运行时节点是 FGNode/FGLink，字段均存在）。
        ref={fgRef as GraphAccessor}
        graphData={graphData}
        {...(width !== undefined ? { width } : {})}
        {...(height !== undefined ? { height } : {})}
        // OrbitControls: 左键绕中心旋转 / 右键拖动平移 (中心跟着走) / 滚轮缩放; 地平线固定不翻滚。不自动旋转。
        controlType="orbit"
        backgroundColor="#0a1628"
        nodeColor={accessNodeColor as GraphAccessor}
        nodeVal={accessNodeVal as GraphAccessor}
        nodeLabel={handleNodeLabel}
        nodeOpacity={0.92}
        nodeResolution={profile.nodeResolution}
        linkColor={accessLinkColor}
        linkOpacity={0.55}
        linkWidth={accessLinkWidth as GraphAccessor}
        linkLabel={handleLinkLabel}
        linkDirectionalArrowLength={3}
        linkDirectionalArrowRelPos={1}
        linkDirectionalParticles={(profile.particles ? accessLinkParticles : 0) as GraphAccessor}
        linkDirectionalParticleWidth={2}
        linkDirectionalParticleSpeed={0.006}
        enableNodeDrag={profile.nodeDrag}
        enableNavigationControls
        showNavInfo={false}
        onNodeClick={handleNodeClickInternal as GraphAccessor}
        // 大图 (instanced): 隐藏 ForceGraph 默认逐节点/逐边对象, 渲染交批量层; tick 回调驱动坐标同步。
        // 小图 (mesh): 逐节点挂文字 sprite, 与默认球体共存 (原行为)。
        {...(isInstanced
          ? {
            nodeVisibility: hiddenAccessor as GraphAccessor,
            linkVisibility: hiddenAccessor as GraphAccessor,
            onEngineTick: handleEngineTick,
            onEngineStop: handleEngineStop,
          }
          : { nodeThreeObject: buildNodeSprite, nodeThreeObjectExtend: true })}
        {...(profile.cooldownTicks !== undefined
          ? { cooldownTicks: profile.cooldownTicks }
          : {})}
      />
      {showLegend ? <Legend /> : null}
      {centerId ? (
        <div
          className="absolute bottom-12 left-12 bg-white px-8 py-4 rounded-6 text-12"
          style={{
            border: '1px solid #f0f0f0',
            color: LANG_COLOR.java,
          }}
        >
          中心节点 · {centerId.slice(0, 20)}
          {centerId.length > 20 ? '...' : ''}
        </div>
      ) : null}
    </div>
  );
};

export default Graph3DCanvas;
