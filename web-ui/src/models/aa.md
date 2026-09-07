     Context

     项目 C:\workspace\project 下 8 个业务模块的现状：                                                                       ◐

     - 代码层面破损：所有模块的 index.tsx 都从 @/services/apis 导入了不存在的 stockApi 命名空间和简化类型名（type SyncJob / RecommendRule / StockPool 等），实际生成的 API 是按
     controller 分文件（configapi.ts、stockapi.ts、dashboardapi.ts），类型用全名 API.XxxResponse/API.XxxRequest。这意味着所有页面目前都编译不过。
     - 规范偏差：硬编码枚举（违反 useModel('enum') 规范）；Columns.tsx 的默认导出与文件名不匹配（违反
     filenames/match-exported）；stocks/ChartDrawer、stocks/IndicatorDrawer、recommendresults/SignalDrawer 用 post('/url') 直拼请求 + 自定义 type 而非生成的 API。
     - 参照样板：D:\FrontendWorkJusd\ai-context-support-web\src\pages\prompt\management 提供命名导出 + requestWrapper + context 注入枚举 + Typography.Link
     操作列的完整样式；syncjobs 模块已按此样板重构完成（lint 通过），作为本项目内的活样板。
     - 目标：把剩余 7 个模块拉到与 syncjobs 一致的形态，让它们能编译、能通 lint、能跑。

     真实 API → 模块映射

     ┌──────────────────┬────────────────────────────┬────────────────────────────────────────────────────────────────────────────────┐
     │       模块       │       真实 API 文件        │                                    主要函数                                    │
     ├──────────────────┼────────────────────────────┼────────────────────────────────────────────────────────────────────────────────┤
     │ dashboard        │ dashboardapi.ts            │ postSummary                                                                    │
     ├──────────────────┼────────────────────────────┼────────────────────────────────────────────────────────────────────────────────┤
     │ stocks           │ stockapi.ts                │ postStocksPage, postQuotes, postIndicators, postSignals                        │
     ├──────────────────┼────────────────────────────┼────────────────────────────────────────────────────────────────────────────────┤
     │ alerts           │ configapi.ts               │ postAlertsPage, postHandle                                                     │
     ├──────────────────┼────────────────────────────┼────────────────────────────────────────────────────────────────────────────────┤
     │ recommendrules   │ configapi.ts               │ postRecommendRulesPage, postRecommendRulesSave, postDelete4                    │
     ├──────────────────┼────────────────────────────┼────────────────────────────────────────────────────────────────────────────────┤
     │ recommendresults │ configapi.ts + stockapi.ts │ postLatestPage, postFromRecommend, postSignals                                 │
     ├──────────────────┼────────────────────────────┼────────────────────────────────────────────────────────────────────────────────┤
     │ stockpools       │ configapi.ts               │ postStockPoolsPage/Save, postDelete2, postStockPoolItemsPage/Save, postDelete3 │
     ├──────────────────┼────────────────────────────┼────────────────────────────────────────────────────────────────────────────────┤
     │ systemconfig     │ configapi.ts               │ postSystemConfigPage, postSystemConfigSave                                     │
     ├──────────────────┼────────────────────────────┼────────────────────────────────────────────────────────────────────────────────┤
     │ tradeplans       │ configapi.ts               │ postTradePlansPage, postTradePlansSave, postDelete, postUpdateStatus           │
     └──────────────────┴────────────────────────────┴────────────────────────────────────────────────────────────────────────────────┘

     枚举使用决策（参照 src\models\enumslocal.tsx）

     ┌────────────────────┬────────────────────────────────────────────────────┬─────────────────────────────────────────────────────────────────────────┐
     │        模块        │             走 useModel('enum') 的枚举             │                 硬编码（enumslocal 中无对应或值不一致）                 │
     ├────────────────────┼────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────┤
     │ syncjobs（已完成） │ SyncStatusEnum、AnalysisSceneEnum                  │ jobType（DAILY_PIPELINE/QUOTE_SYNC/ENRICHMENT_SYNC/RECOMMEND_REFRESH）  │
     ├────────────────────┼────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────┤
     │ dashboard          │ AnalysisSignalEnum、RiskLevelEnum                  │ —                                                                       │
     ├────────────────────┼────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────┤
     │ stocks             │ StockMarketEnum、AnalysisSignalEnum、RiskLevelEnum │ —                                                                       │
     ├────────────────────┼────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────┤
     │ alerts             │ AlertTypeEnum                                      │ severity（INFO/WARN/CRITICAL）                                          │
     ├────────────────────┼────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────┤
     │ recommendrules     │ RiskLevelEnum                                      │ —                                                                       │
     ├────────────────────┼────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────┤
     │ recommendresults   │ AnalysisSignalEnum、RiskLevelEnum                  │ —                                                                       │
     ├────────────────────┼────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────┤
     │ stockpools         │ StockPoolTypeEnum、FocusLevelEnum                  │ —                                                                       │
     ├────────────────────┼────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────┤
     │ systemconfig       │ —                                                  │ —                                                                       │
     ├────────────────────┼────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────┤
     │ tradeplans         │ AnalysisSignalEnum、RiskLevelEnum                  │ planStatus（DRAFT/ACTIVE/CLOSED）、action（BUY/SELL/HOLD）、timing（…） │
     └────────────────────┴────────────────────────────────────────────────────┴─────────────────────────────────────────────────────────────────────────┘

     每个模块的统一改造清单

     对每个模块按相同节奏走（参照已完成的 syncjobs）：

     1. utils.ts
       - 导出强类型行别名 export type XxxRow = API.XxxResponse
       - convertParams：参数类型 Record<string, unknown>，读取 params.pageNum（ResizableTable 已转换）；返回 Partial<API.SimplePageRequest> 或对应分页 DTO
       - 保留纯 UI 映射（如 STATUS_COLOR），删除业务枚举的硬编码 options
     2. Columns.tsx
       - 改为命名导出 export function createColumns(...)，无 default export（规避 filenames/match-exported）
       - props 接 context，传入 xxxOptions（用于 ProTable 搜索 valueType:'select'）和 xxxMap（用于行渲染中文）
       - 操作列用 Typography.Link + 内联箭头（与 management 参考一致）
       - 不在文件内调任何 Hook
     3. ToolBarRender.tsx
       - import { PermissionButton } from '@/components/Button'
       - 默认导出函数名与文件名一致（ToolBarRender）
       - 多回调用对象分组、单回调平铺
     4. XxxFormDrawer / XxxModal
       - 用 @/components/Drawer / @/components/Modal
       - 表单走 Form.useForm + useEffect 在 open 时回填/重置
       - 提交用 onOk 自动 loading（CLAUDE.md 规则）；只在需要三按钮、纯展示等场景自定义 footer
       - 枚举 options 从 props 注入；如需要 confirm 用 showConfirm
       - 调用真实 API 函数
     5. index.tsx
       - 顶部 useModel('enum') 取所需 options/maps，useMemo 缓存
       - actionRef + useState 管理 Drawer/Modal 的 context 对象
       - requestWrapper 包真实 postXxxPage
       - useCallback 包所有 handler；useMemo 包 columns
       - <PageContainer> + <ResizableTable> 配置 search/form/scroll/pagination，与 management 参考一致

     处理顺序（简单 → 复杂）

     1. systemconfig（无枚举、单 ModalForm 3 字段）
     2. alerts（单表+操作列、AlertTypeEnum）
     3. recommendrules（单 Form + RiskLevelEnum）
     4. dashboard（单表 + 4 个 StatisticCard、占位 summary 接口）
     5. tradeplans（12 字段 Form + 多套硬编码枚举聚合到 utils）
     6. recommendresults（含 SignalDrawer，同时把 post() 替换为 postSignals）
     7. stocks（双 Drawer：ChartDrawer 用 ECharts、IndicatorDrawer 表格；同时把 post() 替换为 postQuotes/postIndicators）
     8. stockpools（最复杂：双表联动 + 2 个 Form + 6 个 API 调用）

     每个模块改完跑一次 npx eslint src/pages/<module> 确认通过；最后整体跑一次 lint。

     关键文件

     待改：
     - src\pages\systemconfig\ 4 个文件
     - src\pages\alerts\ 3 个文件（Columns.tsx / index.tsx / utils.ts，无 ToolBarRender）
     - src\pages\recommendrules\ 5 个文件
     - src\pages\dashboard\ 2 个文件（index.tsx / Columns.tsx）
     - src\pages\tradeplans\ 5 个文件
     - src\pages\recommendresults\ 4 个文件（含 SignalDrawer/index.tsx）
     - src\pages\stocks\ 4 个文件（含 ChartDrawer/、IndicatorDrawer/）
     - src\pages\stockpools\ 7 个文件（含 PoolFormDrawer/、PoolItemFormDrawer/）

     参照（只读）：
     - src\pages\syncjobs\** — 项目内活样板
     - D:\FrontendWorkJusd\ai-context-support-web\src\pages\prompt\management\** — 外部参照
     - src\models\enumslocal.tsx — 枚举名/值真值表
     - src\services\apis\typings.d.ts — API 类型定义
     - src\services\apis\{config,stock,dashboard}api.ts — 真实生成的 API 函数

     验证

     每个模块改完：

     cd C:\workspace\project
     npx eslint src/pages/<module>

     8 个模块全部完成后：

     npx eslint src/pages
     pnpm dev   # 用户手动验证：登录后逐个页面打开，确认接口能跑、表单能提交、Drawer 能开

     ▎ tsc 在项目根的现有基线已经报 node_modules/@ant-design/pro-components 内部错误，与本次改动无关；只关注模块自身 lint。

     - D:\FrontendWorkJusd\ai-context-support-web\src\pages\prompt\management\** — 外部参照
     - src\models\enumslocal.tsx — 枚举名/值真值表
     - src\services\apis\typings.d.ts — API 类型定义
     - src\services\apis\{config,stock,dashboard}api.ts — 真实生成的 API 函数

     验证

     每个模块改完：

     cd C:\workspace\project
     npx eslint src/pages/<module>

     8 个模块全部完成后：

     npx eslint src/pages
     pnpm dev   # 用户手动验证：登录后逐个页面打开，确认接口能跑、表单能提交、Drawer 能开

     ▎ tsc 在项目根的现有基线已经报 node_modules/@ant-design/pro-components 内部错误，与本次改动无关；只关注模块自身 lint。
