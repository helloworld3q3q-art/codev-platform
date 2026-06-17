// 批量渲染层 — 大图 (instanced 模式) 的渲染接管层, 纯 three.js, 不掺 React。
//
// 解决"项目越大节点越多越卡"的根因: react-force-graph 默认逐节点一个 Mesh、逐边一条 Line,
// draw call 随规模线性涨 (2 万节点 ≈ 5 万 draw call 直接卡死)。本层改为:
//   · 全部节点  → 1 个 THREE.InstancedMesh   (一次 draw call, 与节点数/球面精度都无关)
//   · 全部边    → 1 个 THREE.LineSegments     (一次 draw call)
//   · 文字标签  → 距离 LOD, 只给近相机节点动态挂 sprite (贴图数恒定在 budget, 不随总量涨)
//   · 节点拾取  → 自己对 InstancedMesh 做 raycast (拿 instanceId → 节点), 替代 ForceGraph 逐对象拾取
//
// 复用 react-force-graph 的力布局/相机/控制器: 节点 x/y/z 由其 d3-force 原地写回传入的 nodes 对象,
// 本层每个仿真 tick 读这些坐标刷新实例矩阵与线段端点 (收敛后坐标不变, 零持续开销)。
// 配套: Graph3DCanvas 在 instanced 模式给 ForceGraph 设 nodeVisibility/linkVisibility = false,
// 让它不产任何逐节点/逐边对象, 渲染完全交给本层。

import * as THREE from 'three';

import { buildNodeLabelSprite } from './utils';

// three 在本项目无类型声明 (无 @types/three, 包自带 d.ts 未被解析), THREE.* 仅能当值用不能当类型。
// 下列别名把本模块用到的 three 对象类型收口 (等价 any), 仅用于内部注解, 不外泄到导出接口。
/* eslint-disable @typescript-eslint/no-explicit-any */
type TScene = any;
type TCamera = any;
type TRenderer = any;
type TInstancedMesh = any;
type TLineSegments = any;
type TBufferGeometry = any;
type TSprite = any;
type TVec3 = any;
/* eslint-enable @typescript-eslint/no-explicit-any */

// 边曲线: 每条边切成贝塞尔曲线段, 让重叠的连线掰出弧度叉开 (看清成束的边)。
// 弧高 = 边长 * AMP → 长边 (跨层) 弧大叉得开, 短边 (雪花内) 几乎仍是直线。
const EDGE_SEGMENTS = 6;
const EDGE_VERTS = EDGE_SEGMENTS * 2; // LineSegments 每段 2 顶点
const EDGE_CURVE_AMP = 0.18;
const UP_VEC = new THREE.Vector3(0, 1, 0);
const RIGHT_VEC = new THREE.Vector3(1, 0, 0);

// 节点球半径公式与 react-force-graph 默认一致 (Math.cbrt(val) * nodeRelSize), 保两模式视觉尺寸连续。
const NODE_REL_SIZE = 4;
const LABEL_REFRESH_MS = 150;
const HOVER_RAYCAST_MS = 80;
const LABEL_Y_OFFSET = 6;
const NODE_OPACITY = 0.92;
const LINK_OPACITY = 0.55;
const FALLBACK_COLOR = '#bfbfbf';
const FALLBACK_LINK_COLOR = '#d9d9d9';

interface LayerNode {
  id?: string;
  name?: string;
  color?: string;
  val?: number;
  x?: number;
  y?: number;
  z?: number;
}

interface LayerLink {
  // 初始为节点 id 字符串, 经 ForceGraph 的 d3-force 处理后原地替换成节点对象。两种都要兼容。
  source: string | LayerNode;
  target: string | LayerNode;
  color?: string;
}

export interface InstancedLayerOptions {
  nodeResolution: number;
  // 同屏标签数: 始终给"离相机最近的 labelBudget 个"节点挂标签 (其余回收), 贴图数恒定不随总量涨。
  labelBudget: number;
  onNodeClick?: (id: string) => void;
  onNodeHover?: (id: string | null) => void;
}

interface NearEntry {
  i: number;
  d: number;
}

function byDistance(a: NearEntry, b: NearEntry): number {
  return a.d - b.d;
}

export default class GraphInstancedLayer {
  private readonly scene: TScene;
  private readonly camera: TCamera;
  private readonly domElement: HTMLElement;
  private readonly nodes: LayerNode[];
  private readonly links: LayerLink[];
  private readonly options: InstancedLayerOptions;

  private readonly nodeMap = new Map<string, LayerNode>();
  private readonly mesh: TInstancedMesh;
  private readonly lineGeom: TBufferGeometry;
  private readonly lines: TLineSegments;
  private readonly linePositions: Float32Array;
  private readonly activeLabels = new Map<number, TSprite>();

  // 复用的临时对象, 避免每帧 new
  private readonly mMatrix = new THREE.Matrix4();
  private readonly mQuat = new THREE.Quaternion();
  private readonly mScale = new THREE.Vector3();
  private readonly mPos = new THREE.Vector3();
  private readonly camPos = new THREE.Vector3();
  // 边曲线临时量 (端点 A/B、方向、垂向、控制点、两个采样点), 复用避免每帧 new
  private readonly eA = new THREE.Vector3();
  private readonly eB = new THREE.Vector3();
  private readonly eDir = new THREE.Vector3();
  private readonly ePerp = new THREE.Vector3();
  private readonly eCtrl = new THREE.Vector3();
  private readonly eP0 = new THREE.Vector3();
  private readonly eP1 = new THREE.Vector3();
  private readonly raycaster = new THREE.Raycaster();
  private readonly pointer = new THREE.Vector2();
  // 距离排序池: 预分配 = 节点数, 每次刷新就地复用 (不每帧 new, 避免大图 GC 抖动)。
  private readonly near: NearEntry[];

  private rafId = 0;
  private lastLabelAt = 0;
  private lastHoverAt = 0;
  private hoveredId: string | null = null;
  private readonly onMove: (e: PointerEvent) => void;
  private readonly onClick: (e: MouseEvent) => void;

  constructor(
    scene: TScene,
    camera: TCamera,
    renderer: TRenderer,
    nodes: LayerNode[],
    links: LayerLink[],
    options: InstancedLayerOptions,
  ) {
    this.scene = scene;
    this.camera = camera;
    this.domElement = renderer.domElement;
    this.nodes = nodes;
    this.links = links;
    this.options = options;
    this.near = new Array(nodes.length);
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      if (n.id) this.nodeMap.set(n.id, n);
      this.near[i] = { i, d: 0 };
    }

    this.mesh = this.buildNodeMesh();
    // 每条边 EDGE_VERTS 个顶点 (曲线段), 每顶点 3 分量
    this.linePositions = new Float32Array(links.length * EDGE_VERTS * 3);
    this.lineGeom = new THREE.BufferGeometry();
    this.lines = this.buildLines();
    this.scene.add(this.mesh);
    this.scene.add(this.lines);
    this.syncPositions();

    this.onMove = (e) => {
      this.handleMove(e);
    };
    this.onClick = (e) => {
      this.handleClick(e);
    };
    this.domElement.addEventListener('pointermove', this.onMove, { passive: true });
    this.domElement.addEventListener('click', this.onClick);
    this.startLabelLoop();
  }

  private buildNodeMesh(): TInstancedMesh {
    const res = this.options.nodeResolution;
    const geometry = new THREE.SphereGeometry(1, res, res);
    const material = new THREE.MeshLambertMaterial({ transparent: true, opacity: NODE_OPACITY });
    const mesh = new THREE.InstancedMesh(geometry, material, this.nodes.length);
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    // 实例分布广, 关整体视锥剔除 (否则相机贴近时整块可能被误剔)
    mesh.frustumCulled = false;
    const color = new THREE.Color();
    for (let i = 0; i < this.nodes.length; i++) {
      color.set(this.nodes[i].color ?? FALLBACK_COLOR);
      mesh.setColorAt(i, color);
    }
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    return mesh;
  }

  private buildLines(): TLineSegments {
    const stride = EDGE_VERTS * 3;
    const colors = new Float32Array(this.links.length * stride);
    const color = new THREE.Color();
    for (let i = 0; i < this.links.length; i++) {
      color.set(this.links[i].color ?? FALLBACK_LINK_COLOR);
      const base = i * stride;
      for (let v = 0; v < EDGE_VERTS; v++) {
        const o = base + v * 3;
        colors[o] = color.r; colors[o + 1] = color.g; colors[o + 2] = color.b;
      }
    }
    const posAttr = new THREE.BufferAttribute(this.linePositions, 3);
    posAttr.setUsage(THREE.DynamicDrawUsage);
    this.lineGeom.setAttribute('position', posAttr);
    this.lineGeom.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    const material = new THREE.LineBasicMaterial({
      vertexColors: true,
      transparent: true,
      opacity: LINK_OPACITY,
    });
    const lines = new THREE.LineSegments(this.lineGeom, material);
    lines.frustumCulled = false;
    return lines;
  }

  private radiusOf(n: LayerNode): number {
    return Math.cbrt(Math.max(n.val ?? 1, 1)) * NODE_REL_SIZE;
  }

  private endpoint(ref: string | LayerNode): LayerNode | undefined {
    return typeof ref === 'string' ? this.nodeMap.get(ref) : ref;
  }

  // 每个仿真 tick 调用: 把节点最新坐标刷进实例矩阵 + 线段端点。收敛后 ForceGraph 不再回调, 自然停更。
  syncPositions(): void {
    for (let i = 0; i < this.nodes.length; i++) {
      const n = this.nodes[i];
      this.mPos.set(n.x ?? 0, n.y ?? 0, n.z ?? 0);
      const r = this.radiusOf(n);
      this.mScale.set(r, r, r);
      this.mMatrix.compose(this.mPos, this.mQuat, this.mScale);
      this.mesh.setMatrixAt(i, this.mMatrix);
    }
    this.mesh.instanceMatrix.needsUpdate = true;
    // raycast 拾取依赖整体包围球, 随坐标更新重算
    this.mesh.computeBoundingSphere();

    for (let i = 0; i < this.links.length; i++) {
      this.writeCurvedEdge(
        i,
        this.endpoint(this.links[i].source),
        this.endpoint(this.links[i].target),
      );
    }
    this.lineGeom.attributes.position.needsUpdate = true;
  }

  // 把第 i 条边写成一条贝塞尔曲线 (端点 A→B, 控制点 = 中点沿垂向抬 len*AMP), 切 EDGE_SEGMENTS 段。
  private writeCurvedEdge(i: number, s: LayerNode | undefined, t: LayerNode | undefined): void {
    this.eA.set(s?.x ?? 0, s?.y ?? 0, s?.z ?? 0);
    this.eB.set(t?.x ?? 0, t?.y ?? 0, t?.z ?? 0);
    this.eDir.subVectors(this.eB, this.eA);
    const len = this.eDir.length();
    // 垂向: 边方向叉乘 up; 与 up 近平行时退化, 改叉乘 right
    this.ePerp.crossVectors(this.eDir, UP_VEC);
    if (this.ePerp.lengthSq() < 1e-6) this.ePerp.crossVectors(this.eDir, RIGHT_VEC);
    this.ePerp.normalize();
    this.eCtrl
      .addVectors(this.eA, this.eB)
      .multiplyScalar(0.5)
      .addScaledVector(this.ePerp, len * EDGE_CURVE_AMP);
    const pos = this.linePositions;
    let base = i * EDGE_VERTS * 3;
    this.bezierAt(this.eP0, 0);
    for (let k = 1; k <= EDGE_SEGMENTS; k++) {
      this.bezierAt(this.eP1, k / EDGE_SEGMENTS);
      pos[base] = this.eP0.x; pos[base + 1] = this.eP0.y; pos[base + 2] = this.eP0.z;
      pos[base + 3] = this.eP1.x; pos[base + 4] = this.eP1.y; pos[base + 5] = this.eP1.z;
      base += 6;
      this.eP0.copy(this.eP1);
    }
  }

  // 二次贝塞尔在 t 处求值 (端点 eA/eB, 控制点 eCtrl), 写入 out。
  private bezierAt(out: TVec3, tt: number): void {
    const u = 1 - tt;
    const w0 = u * u;
    const w1 = 2 * u * tt;
    const w2 = tt * tt;
    out.set(
      w0 * this.eA.x + w1 * this.eCtrl.x + w2 * this.eB.x,
      w0 * this.eA.y + w1 * this.eCtrl.y + w2 * this.eB.y,
      w0 * this.eA.z + w1 * this.eCtrl.z + w2 * this.eB.z,
    );
  }

  private startLabelLoop(): void {
    const tick = (now: number): void => {
      if (now - this.lastLabelAt >= LABEL_REFRESH_MS) {
        this.lastLabelAt = now;
        this.refreshLabels();
      }
      this.rafId = window.requestAnimationFrame(tick);
    };
    this.rafId = window.requestAnimationFrame(tick);
  }

  // 距离 LOD: 按到相机距离排序, 取最近 budget 个节点挂标签, 其余回收。
  // 不设硬距离截断 → 任何缩放都能看到名称 (近处的那批)。池就地复用, 无每帧分配。
  private refreshLabels(): void {
    this.camera.getWorldPosition(this.camPos);
    for (let i = 0; i < this.nodes.length; i++) {
      const n = this.nodes[i];
      const dx = (n.x ?? 0) - this.camPos.x;
      const dy = (n.y ?? 0) - this.camPos.y;
      const dz = (n.z ?? 0) - this.camPos.z;
      const e = this.near[i];
      e.i = i;
      e.d = dx * dx + dy * dy + dz * dz;
    }
    this.near.sort(byDistance);
    const limit = Math.min(this.options.labelBudget, this.near.length);
    const keep = new Set<number>();
    for (let k = 0; k < limit; k++) keep.add(this.near[k].i);
    this.applyLabels(keep);
  }

  private applyLabels(keep: Set<number>): void {
    this.activeLabels.forEach((_sprite: TSprite, idx: number) => {
      if (!keep.has(idx)) this.removeLabel(idx);
    });
    keep.forEach((idx) => {
      const sprite = this.activeLabels.get(idx) ?? this.createLabel(idx);
      if (sprite) {
        const n = this.nodes[idx];
        sprite.position.set(n.x ?? 0, (n.y ?? 0) + LABEL_Y_OFFSET, n.z ?? 0);
      }
    });
  }

  private createLabel(idx: number): TSprite | undefined {
    const n = this.nodes[idx];
    const sprite = buildNodeLabelSprite(n.name, n.color);
    if (!sprite) return undefined;
    this.scene.add(sprite);
    this.activeLabels.set(idx, sprite);
    return sprite;
  }

  private removeLabel(idx: number): void {
    const sprite = this.activeLabels.get(idx);
    if (!sprite) return;
    this.scene.remove(sprite);
    this.disposeSprite(sprite);
    this.activeLabels.delete(idx);
  }

  private disposeSprite(sprite: TSprite): void {
    const material = sprite.material;
    if (material.map) material.map.dispose();
    material.dispose();
  }

  private pick(clientX: number, clientY: number): LayerNode | undefined {
    const rect = this.domElement.getBoundingClientRect();
    this.pointer.x = ((clientX - rect.left) / rect.width) * 2 - 1;
    this.pointer.y = -((clientY - rect.top) / rect.height) * 2 + 1;
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hits = this.raycaster.intersectObject(this.mesh, false);
    const first = hits[0];
    if (first && first.instanceId !== undefined && first.instanceId !== null) {
      return this.nodes[first.instanceId];
    }
    return undefined;
  }

  private handleMove(e: PointerEvent): void {
    const now = performance.now();
    if (now - this.lastHoverAt < HOVER_RAYCAST_MS) return;
    this.lastHoverAt = now;
    const node = this.pick(e.clientX, e.clientY);
    const id = node?.id ?? null;
    if (id === this.hoveredId) return;
    this.hoveredId = id;
    this.domElement.style.cursor = id ? 'pointer' : '';
    this.options.onNodeHover?.(id);
  }

  private handleClick(e: MouseEvent): void {
    const node = this.pick(e.clientX, e.clientY);
    if (node?.id) this.options.onNodeClick?.(node.id);
  }

  dispose(): void {
    window.cancelAnimationFrame(this.rafId);
    this.domElement.removeEventListener('pointermove', this.onMove);
    this.domElement.removeEventListener('click', this.onClick);
    this.domElement.style.cursor = '';
    this.activeLabels.forEach((sprite: TSprite) => {
      this.scene.remove(sprite);
      this.disposeSprite(sprite);
    });
    this.activeLabels.clear();
    this.scene.remove(this.mesh);
    this.scene.remove(this.lines);
    this.mesh.geometry.dispose();
    this.mesh.material.dispose();
    this.lineGeom.dispose();
    this.lines.material.dispose();
    this.nodeMap.clear();
  }
}
