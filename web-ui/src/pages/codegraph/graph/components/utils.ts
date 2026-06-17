// Graph3DCanvas 的无状态辅助: 渲染档位决策 (纯函数) + 节点标签 sprite 构造。
// 有状态的批量渲染层在 GraphInstancedLayer.ts。

import * as THREE from 'three';

// three 在本项目无类型声明 (无 @types/three), THREE.Sprite 仅能当值用; 别名收口返回类型 (等价 any)。
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type TSprite = any;

// ============ 渲染档位 (规模 → 策略, 集中在纯函数, 不在各处散 if) ============
//
// 两档:
//   mesh      — 小图: react-force-graph 默认逐节点球 + 文字 sprite + 粒子 + 拖拽。零回归。
//   instanced — 大图: 自建 InstancedMesh (全部球一次 draw call) + LineSegments (全部边一次
//               draw call) + 距离 LOD 标签。draw call 与节点数脱钩, 项目再大也不随之线性涨。

export type RenderMode = 'mesh' | 'instanced';

export interface RenderProfile {
  mode: RenderMode;
  // 球面分段数。instanced 下成本与精度脱钩 (GPU instancing), 故保持与 mesh 同等高精度, 不降质量。
  nodeResolution: number;
  // 是否给 calls 边挂方向粒子动画 (大图关掉, 省持续渲染负载)。
  particles: boolean;
  // 是否启用节点拖拽 (instanced 下无逐节点对象可抓, 大图关掉)。
  nodeDrag: boolean;
  // 标签数: mesh 全显 (= nodeCount); instanced 只给离相机最近的这么多个节点挂标签 (贴图数恒定, 不随总量涨)。
  labelBudget: number;
  // 仿真冷却 tick 上限。大图限制收敛步数, 防长时间高 CPU; undefined = 用库默认 (mesh 不限)。
  cooldownTicks?: number;
}

// 阈值: 节点数 > 此值切到 instanced 批量渲染。阈值以下保持原 mesh 行为 (零回归)。
// codegraph 概览固定拉 2000 节点 → 走 instanced (一并提速); 典型邻居展开多 < 该值 → 走 mesh。
const INSTANCED_THRESHOLD = 1500;

export function resolveRenderProfile(nodeCount: number): RenderProfile {
  if (nodeCount <= INSTANCED_THRESHOLD) {
    return {
      mode: 'mesh',
      nodeResolution: 16,
      particles: true,
      nodeDrag: true,
      labelBudget: nodeCount,
      cooldownTicks: undefined,
    };
  }
  return {
    mode: 'instanced',
    // 精度不降: instancing 后球面分段数不影响 draw call, 与 mesh 同 16。
    nodeResolution: 16,
    particles: false,
    nodeDrag: false,
    // 始终给"离相机最近的 labelBudget 个"节点挂标签 (不设硬距离截断), 保证任何缩放都看得到名称。
    // 超大图压低防杂乱; 数千节点放宽到能看清更多名称。
    labelBudget: nodeCount > 8000 ? 80 : 160,
    // 大图限制收敛步数, 几秒内出布局; 超大图再收紧。
    cooldownTicks: nodeCount > 12000 ? 250 : 400,
  };
}

// ============ 节点文字标签 sprite (mesh 逐节点挂 / instanced 距离 LOD 共用, 避免重复) ============
//
// 用 CanvasTexture 直接画贴图 (不引 three-spritetext)。返回 null 表示无名字 (调用方决定兜底:
// mesh 用空 Object3D, instanced 直接跳过)。

const FONT = '600 28px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
const FONT_SIZE = 28;
const PAD_X = 12;
const PAD_Y = 6;
const MAX_LEN = 24;
// 1 像素 ≈ 0.25 世界单位 (与默认球体视觉协调)。
const PX_TO_WORLD = 0.25;

export function buildNodeLabelSprite(name?: string, color?: string): TSprite | null {
  const raw = name ?? '';
  if (!raw) return null;
  // 截断长名避免 sprite 过宽
  const label = raw.length > MAX_LEN ? `${raw.slice(0, MAX_LEN)}…` : raw;
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  ctx.font = FONT;
  const textWidth = ctx.measureText(label).width;
  canvas.width = Math.ceil(textWidth + PAD_X * 2);
  canvas.height = FONT_SIZE + PAD_Y * 2;
  // canvas 调整大小后 ctx 状态重置, 重新设置字体
  ctx.font = FONT;
  ctx.textBaseline = 'middle';
  // 半透明深色背景增强对比 (深底浅字方案)
  ctx.fillStyle = 'rgba(10, 22, 40, 0.78)';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = color ?? '#ffffff';
  ctx.fillText(label, PAD_X, canvas.height / 2);

  const texture = new THREE.CanvasTexture(canvas);
  texture.needsUpdate = true;
  const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false });
  const sprite = new THREE.Sprite(material);
  sprite.scale.set(canvas.width * PX_TO_WORLD, canvas.height * PX_TO_WORLD, 1);
  // 默认本地偏移 (mesh 模式作为节点子对象时浮在节点上方; instanced 模式调用方会改写为世界坐标)
  sprite.position.set(0, 6, 0);
  return sprite;
}
