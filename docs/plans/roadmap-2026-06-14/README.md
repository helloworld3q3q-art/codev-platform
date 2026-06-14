# roadmap-2026-06-14 迭代规划目录

> **主题**:平台**产品化** —— 从"平台自用的代码智能系统闭环" → "让别的业务仓/团队简单接入并用起来"。
>
> 代码智能平台线(roadmap-2026-06-07)判定到平台期后,主线转产品化。本轮**主动解冻**
> roadmap-2026-06-10 的两个 Gate 项 + 推进 Phase 10 治理。核心驱动 = 用户痛点"加入项目流程繁琐"。

## 文件清单

| 文件 | 内容 | 状态 |
|---|---|---|
| [platform-productization-plan-2026-06-14.md](platform-productization-plan-2026-06-14.md) | 主 plan:P1 onboard / P2 多组织 web 签发 / P3 多仓多根索引 / P4 Phase 10 治理 | P1✅ P4✅ P2🚧 P3 待 |
| [daily-summary-2026-06-14.md](daily-summary-2026-06-14.md) | 产品化迭代 Day 1:onboard/治理/token 起步 + 贯穿教训"先核实避免重造" | 日报 |

## 四项速览(优先级序)

| 序 | 项 | 重量 | 继承 |
|---|---|---|---|
| 1 | **P1 onboard**(加项目 8 步→1 步)✅**已交付** `76d5ea8`(onboard 命令已存在,补 sync+.mcp.json 2 软步)| 轻 | roadmap-2026-06-07 Phase 10 |
| 2 | **P4 Phase 10 治理** ✅**已交付**(接入指南重写成一键 onboard;治理看板已存在;能力矩阵 enabled 伪需求不做)| 轻 | roadmap-2026-06-07 Phase 10 |
| 3 | **P2 多组织 web 签发 UI** 🚧**进行中**(核实:唯一缺 token 签发 web 化;`issue_token` 共享已交付 `379f42c`,web 路由设计就绪)| 轻(收窄) | roadmap-2026-06-10 多组织 Phase 2(解冻)|
| 4 | **P3 多仓多根索引**(codegraph/code_vec fan-out,最重)| 重 | roadmap-2026-06-10 多仓 Phase 2(解冻)|

## 关联

- 上轮:[`../roadmap-2026-06-07/`](../roadmap-2026-06-07/)(代码智能平台,已到平台期)+ [`../roadmap-2026-06-10/`](../roadmap-2026-06-10/)(多组织/多仓 Phase 1 已建,Phase 2 本轮解冻)
- 共同纪律见主 plan §六:编排复用不堆码 / 不自动跑全量索引 / web schema→run api / 多组织安全红线 / 分阶段可验。
