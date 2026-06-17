# daily-summary 2026-06-17 —— web-ui 图谱大图渲染优化(批量渲染 + 层级聚类 + 曲线边 + Orbit)

> 主题与 P1-P4 产品化主线并行的**前端体验线**:统一图谱(unifiedgraph)+ 节点图谱(codegraph/graph)在 ideas-v2(23642 节点 / 28863 边)下渲染卡死。
> 两页**共用同一个 `Graph3DCanvas`** → 改一处两页受益(低耦合 + 单一职责)。贯穿纪律:**先核实第三方行为再动手、复用单一真值源、验证驱动找真根因**。

## 一、批量渲染(性能根治)

**根因**:react-force-graph 默认逐节点一个 Mesh + 逐节点一张 canvas 文字贴图 + 逐边一条 Line → 2.3 万节点 ≈ **5 万 draw call** 直接卡死(文字贴图各自独立纹理是头号杀手)。

**方案**(全落在 `Graph3DCanvas` + 新 `GraphInstancedLayer.ts` + `utils.ts`,两页自动共享):

- **渲染档位纯函数** `resolveRenderProfile(nodeCount)`:≤1500 走 `mesh`(原行为零回归);>1500 走 `instanced`。规模→策略集中一处,不散 `if`。
- **instanced 批量层**:全部节点 = 1 个 `InstancedMesh`(一次 draw call,球面精度不降)+ 全部边 = 1 个 `LineSegments`(一次 draw call)+ 文字标签**距离 LOD**(只给最近 budget 个挂 sprite,预分配池防 GC)+ 自己对 InstancedMesh **raycast 拾取**。配套给 ForceGraph 设 `nodeVisibility/linkVisibility=false` 不产任何默认对象。
- 效果:**draw call 与节点数脱钩**(节点+边各 1 次),项目再大也不线性涨。

## 二、instanced 衍生的两个真 bug(验证驱动)

| # | 现象 | 根因 | 修复 |
|---|---|---|---|
| 1 | **节点变少** | `zoomToFit` 靠**场景里带 geometry 的节点对象**算包围盒;instanced 下 `nodeVisibility=false` 无节点对象 → bbox 空/误把原点单位球当全图 → 相机猛缩,节点挤出屏幕 | instanced 弃用 zoomToFit,改 `fitCameraToNodes` 用**节点真实坐标**算 bbox 取景(600ms 早期 + `onEngineStop` 精确各一次) |
| 2 | **没有名称** | LOD 硬距离阈值在概览缩放下把全部标签挡掉 | 去硬截断,改"**始终显示离相机最近的 budget 个**";池就地复用零分配 |

## 三、布局探索:数据形态决定可行性

用户要"节点有点顺序(sql→后端→前端)"。试错三轮:

1. **固定 `fy` 平面分层** → 同层压成一张纸,撤。
2. **软 slab 容纳** → 在 ideas-v2 上摊成"一块饼",撤。**验证发现真相**:图 **82% 是 db_column(19443/23642)**,数据库层独大 —— **任何"拉向单一轴值"的力对超大层必然压平面**(charge 在近共面时无纵向恢复力)。
3. **终方案 = 层级聚类(点吸引,非轴约束)**:每层一个 3D 锚点沿 X 排开(数据库→后端→前端),节点被拉向**锚点**(三轴弹簧)+ charge 排斥 = 每层一个 **3D 球团**(不压平),球团内表-字段小雪花靠连线自然保留。复用 `unifiedLayerOf` 单一真值源派生 `unifiedClusterOf`。

> **诚实边界**:DB 球团 2 万节点天生大,聚类只能让三段有序分开,真正"看得清"仍需在 KindFilter 关掉「字段」层(只剩 ~4200 节点)。布局改不动数据规模。

## 四、曲线边 + Orbit 控制器

- **曲线边**:批量层每条边改**二次贝塞尔**(弧高 = 边长 × 0.18),长边(跨层)叉开成束的连线、短边(雪花内)近直;仍**一次 draw call**(顶点 2→12/边)。
- **控制器 trackball → OrbitControls**:左键绕中心旋转 / **右键拖动平移(旋转中心跟着走)** / 滚轮缩放,地平线固定不翻滚;点节点把旋转中心落到该节点。**关掉自动旋转**(trackball 下本就没生效,orbit 下会真转、干扰定位),并清掉随之无用的 hover 暂停 + 5 秒锁定逻辑。

## 五、教训沉淀

1. **共享组件 = 优化一处两页受益**:渲染逻辑全收进唯一的 `Graph3DCanvas` + 两个无状态/有状态辅助文件,不在两页堆重复码。
2. **数据形态决定布局可行性**:单层占 82% 时,任何全局轴向布局必摊饼;真正 declutter 是**筛掉细节层(字段)**而非换布局。
3. **"拉向单一 Y"本质是压平面**:要保 3D 厚度得用**点吸引 / 区间容纳软墙**,不是调小强度(再小也只是塌得慢)。
4. **第三方封装的隐藏耦合**:react-force-graph `zoomToFit` 依赖场景节点 geometry,自定义 instanced(隐藏默认节点)即失效 —— 动第三方默认渲染前先核实它的内部依赖。

## 旋钮 + 验证

- 可调常量都在 `Graph3DCanvas.tsx` / `GraphInstancedLayer.ts` 顶部:instanced 阈值 1500、`CLUSTER_SPACING/STRENGTH`、`EDGE_CURVE_AMP/SEGMENTS`、`labelBudget`。
- 验证:`tsc` + `eslint` 三文件全过;dev server(Windows :8000)HMR 手测两页(用户逐轮确认)。前端按本仓规则不写单测([[frontend-no-unit-tests]]),靠 tsc/lint 静态门禁。
