# roadmap-2026-06-14 迭代规划目录

> **主题**:平台**产品化** —— 从"平台自用的代码智能系统闭环" → "让别的业务仓/团队简单接入并用起来"。
>
> 代码智能平台线(roadmap-2026-06-07)判定到平台期后,主线转产品化。本轮**主动解冻**
> roadmap-2026-06-10 的两个 Gate 项 + 推进 Phase 10 治理。核心驱动 = 用户痛点"加入项目流程繁琐"。

## 文件清单

| 文件 | 内容 | 状态 |
|---|---|---|
| [platform-productization-plan-2026-06-14.md](platform-productization-plan-2026-06-14.md) | 主 plan:P1 onboard / P2 多组织 web 签发 / P3 多仓多根索引 / P4 Phase 10 治理 | P1✅ P4✅ P2✅ P3 部分完成 |
| [daily-summary-2026-06-14.md](daily-summary-2026-06-14.md) | 产品化迭代 Day 1:onboard/治理/token 起步 + 贯穿教训"先核实避免重造" | 日报 |
| [daily-summary-2026-06-15.md](daily-summary-2026-06-15.md) | Day 2:P2 token web 化收尾(后端路由+前端页)+ Hibernate HBM/HQL 图谱抽取(影响面根治:db_table 0→1117,实体类名→表桥) | 日报 |
| [daily-summary-2026-06-17.md](daily-summary-2026-06-17.md) | 前端体验线:统一图谱/节点图谱大图渲染优化(批量渲染 draw call 5万→2、取景/标签修复、层级聚类球团、曲线边、Orbit 平移);共用 Graph3DCanvas 一处两页受益 | 日报 |
| [daily-summary-2026-06-21.md](daily-summary-2026-06-21.md) | 运维事故线:platform-docs MCP CUDA daemon 故障修复(cudaErrorUnknown 上下文损坏致进程假活/双绿但搜索全死);两步闭环 restart daemon + 重启客户端;教训"进程绿灯≠服务可用,判活必实调" | 日报 |
| [daily-summary-2026-07-06.md](daily-summary-2026-07-06.md) | P3 多仓多根索引首批落地:RepoSpec 真值源、code_vec/recall fan-out、extra repo 反向触发、agent codegraph 工具跨仓查询;剩 Web GraphAPI / 外部 codegraph MCP 代理读侧待做 | 日报 |
| [daily-summary-2026-07-07.md](daily-summary-2026-07-07.md) | P3 审计修复:tagged file tools、rerank extra ref、集中式 codegraph fallback、extra repo sync freshness、git 输出脱敏和边界补测 | 日报 |

## 四项速览(优先级序)

| 序 | 项 | 重量 | 继承 |
|---|---|---|---|
| 1 | **P1 onboard**(加项目 8 步→1 步)✅**已交付** `76d5ea8`(onboard 命令已存在,补 sync+.mcp.json 2 软步)| 轻 | roadmap-2026-06-07 Phase 10 |
| 2 | **P4 Phase 10 治理** ✅**已交付**(接入指南重写成一键 onboard;治理看板已存在;能力矩阵 enabled 伪需求不做)| 轻 | roadmap-2026-06-07 Phase 10 |
| 3 | **P2 多组织 web 签发 UI** ✅**已交付**(token 签发/列出/吊销 后端路由 `a6bc08f` + 前端页 `6260396` + openapi dev 放行 `2e977a5`;list/revoke 补 org 隔离)| 轻(收窄) | roadmap-2026-06-10 多组织 Phase 2(解冻)|
| 4 | **P3 多仓多根索引** ✅**首批已交付 + 审计修复完成**(`0812b8b`/`0a94264`/`d00de6f` + 2026-07-07 tagged path/rerank/fallback/脱敏补齐;Web GraphAPI/MCP 代理待后续)| 重 | roadmap-2026-06-10 多仓 Phase 2(解冻)|

## 关联

- 上轮:[`../roadmap-2026-06-07/`](../roadmap-2026-06-07/)(代码智能平台,已到平台期)+ [`../roadmap-2026-06-10/`](../roadmap-2026-06-10/)(多组织/多仓 Phase 1 已建,Phase 2 本轮解冻)
- 共同纪律见主 plan §六:编排复用不堆码 / 不自动跑全量索引 / web schema→run api / 多组织安全红线 / 分阶段可验。
