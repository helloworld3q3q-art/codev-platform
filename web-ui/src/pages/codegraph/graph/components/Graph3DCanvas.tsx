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
}

// 默认按原始 kind 显示 (codegraph 页保持 [class] / [java_endpoint] 原样)。
function defaultKindLabel(kind?: string): string {
  return kind ?? '';
}

// react-force-graph 要求 nodes/links 平坦字段；把 NodeDTO/EdgeDTO 透传 + 注入颜色/大小。
// x/y/z 由 react-force-graph 物理仿真运行时回写到同一对象（声明为可选以便读取）。
interface FGNode extends NodeDTO {
  color: string;
  val: number;
  x?: number;
  y?: number;
  z?: number;
}

interface FGLink {
  source: string;
  target: string;
  kind?: string;
  color: string;
  particles: number;
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
}) => {
  // 用 unknown 收口 — react-force-graph-3d 的 ref 类型未导出（forwardRef 实例），
  // 通过 ref.current.controls() 拿 OrbitControls 实例
  const fgRef = useRef<unknown>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
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
      .map((e: EdgeDTO) => ({
        source: e.source ?? '',
        target: e.target ?? '',
        kind: e.kind,
        color: edgeColorOf(e.kind),
        particles: e.kind === 'calls' ? 2 : 0,
      }));
    return { nodes, links };
  }, [data, nodeColorFn, nodeSizeFn]);

  // 用户主动锁定（点击节点后 5 秒内不被 hover 重启自动旋转）
  const autoRotateLockedUntilRef = useRef<number>(0);

  // 飞行到指定节点（onClick 直接传入带 x/y/z 的 node 对象,最可靠）
  const flyToNode = useCallback(
    (node: { x?: number; y?: number; z?: number }): void => {
      const ref = fgRef.current as
        | {
            cameraPosition?: (
              pos: { x: number; y: number; z: number },
              target: { x: number; y: number; z: number },
              ms: number,
            ) => void;
            controls?: () => { autoRotate?: boolean };
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
      // 锁 5 秒禁止自动旋转
      autoRotateLockedUntilRef.current = Date.now() + 5000;
      const controls = ref.controls?.();
      if (controls) controls.autoRotate = false;
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

  // hover 暂停自动旋转(锁定期内禁止重启)
  const handleNodeHover = useCallback((node: unknown): void => {
    const ref = fgRef.current as { controls?: () => { autoRotate?: boolean } } | null;
    if (!ref?.controls) return;
    const controls = ref.controls();
    if (!controls) return;
    if (node) {
      controls.autoRotate = false;
    } else if (Date.now() > autoRotateLockedUntilRef.current) {
      controls.autoRotate = true;
    }
  }, []);

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

  // 生成每个节点的文字标签 Sprite — 用 CanvasTexture 直接画到贴图，避免引入 three-spritetext。
  // nodeThreeObjectExtend=true 让 sprite 与默认球体共存。
  // 返回 three 的 Object3D（three 未装类型声明，按 any 模块处理，故标注为 object）。
  const buildNodeSprite = useCallback((node: unknown): object => {
    const n = node as { name?: string; color?: string; kind?: string };
    const rawName = n.name ?? '';
    if (!rawName) return new THREE.Object3D();
    // 截断长名避免 sprite 过宽
    const label = rawName.length > 24 ? `${rawName.slice(0, 24)}…` : rawName;
    const fontSize = 28;
    const padX = 12;
    const padY = 6;
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    if (!ctx) return new THREE.Object3D();
    ctx.font = `600 ${fontSize}px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`;
    const textWidth = ctx.measureText(label).width;
    canvas.width = Math.ceil(textWidth + padX * 2);
    canvas.height = fontSize + padY * 2;
    // canvas 调整大小后 ctx 状态重置，重新设置字体
    ctx.font = `600 ${fontSize}px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`;
    ctx.textBaseline = 'middle';
    // 半透明深色描边背景增强对比（深底浅字方案）
    ctx.fillStyle = 'rgba(10, 22, 40, 0.78)';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = n.color ?? '#ffffff';
    ctx.fillText(label, padX, canvas.height / 2);

    const texture = new THREE.CanvasTexture(canvas);
    texture.needsUpdate = true;
    const material = new THREE.SpriteMaterial({
      map: texture,
      transparent: true,
      depthWrite: false,
    });
    const sprite = new THREE.Sprite(material);
    // 1 像素 ≈ 0.25 世界单位（视觉上和默认球体协调）
    sprite.scale.set(canvas.width * 0.25, canvas.height * 0.25, 1);
    // sprite 浮在节点上方
    sprite.position.set(0, 6, 0);
    return sprite;
  }, []);
  const accessLinkColor = useCallback(
    (l: { color?: string }): string => l.color ?? '#d9d9d9',
    [],
  );
  const accessLinkParticles = useCallback(
    (l: { particles?: number }): number => l.particles ?? 0,
    [],
  );

  // 启动自动旋转 + 聚焦 center 节点
  useEffect(() => {
    const ref = fgRef.current as
      | {
          controls?: () => { autoRotate?: boolean; autoRotateSpeed?: number };
          cameraPosition?: (pos: { x: number; y: number; z: number }, target?: unknown, ms?: number) => void;
        }
      | null;
    if (!ref?.controls) return;
    const controls = ref.controls();
    if (controls) {
      controls.autoRotate = true;
      controls.autoRotateSpeed = 0.6;
    }
    // 数据更新后等一帧再 zoomToFit
    if (data && data.nodes && data.nodes.length > 0 && ref.cameraPosition) {
      const timer = window.setTimeout(() => {
        const r = fgRef.current as {
          zoomToFit?: (ms: number, padding: number) => void;
        } | null;
        r?.zoomToFit?.(800, 80);
      }, 600);
      return () => window.clearTimeout(timer);
    }
    return undefined;
  }, [data]);

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
        backgroundColor="#0a1628"
        nodeColor={accessNodeColor as GraphAccessor}
        nodeVal={accessNodeVal as GraphAccessor}
        nodeLabel={handleNodeLabel}
        nodeThreeObject={buildNodeSprite}
        nodeThreeObjectExtend
        nodeOpacity={0.92}
        nodeResolution={16}
        linkColor={accessLinkColor}
        linkOpacity={0.55}
        linkWidth={1}
        linkLabel={handleLinkLabel}
        linkDirectionalArrowLength={3}
        linkDirectionalArrowRelPos={1}
        linkDirectionalParticles={accessLinkParticles}
        linkDirectionalParticleWidth={2}
        linkDirectionalParticleSpeed={0.006}
        enableNodeDrag
        enableNavigationControls
        showNavInfo={false}
        onNodeClick={handleNodeClickInternal as GraphAccessor}
        onNodeHover={handleNodeHover}
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
