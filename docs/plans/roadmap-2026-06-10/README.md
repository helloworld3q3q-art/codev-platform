# roadmap-2026-06-10 独立课题目录

> **定位**: 2026-06-10 专家面板触发的**独立课题集**(各自成轨),**独立于** `roadmap-2026-06-07`(代码智能平台主线),
> 不继承上轮累积期纪律。共同节奏:专家面板收敛 + validate-first/ROI 分段(立即做的最小切片 + Gate 冻结重型)。

## 文件清单

| 文件 | 内容 | 状态 |
|---|---|---|
| [multi-repo-contract-bridge-plan-2026-06-10.md](multi-repo-contract-bridge-plan-2026-06-10.md) | **多仓跨层链路**:4 专家面板收敛 = Phase 1 operationId 契约桥 + 契约漂移立即做(不分仓也受益,顺修多服务 URL 串台 bug);Phase 2 多根索引冻结到 Gate(≥2 真实多仓项目被卡)。 | 计划(待起 Phase 1) |
| [multi-org-server-identity-plan-2026-06-10.md](multi-org-server-identity-plan-2026-06-10.md) | **多组织 server IDE agent 身份/认证**:4 高级开发面板收敛 = 选 A(IDE 带 token 直连,否决 web 前置 B);认证用 token、授权用共享 PG RBAC 实时查;Phase 1 最小(bind_host+护栏+config token runbook)立即做,Phase 2 PG token 子系统冻结到 Gate(≥2 org / ≥3 dev)。含数据模型对齐(org/项目/token/memory 同一 PG 真值源)。 | 计划(待起 Phase 1) |

## 背景一句话

平台硬假设"1 project = 1 repo",跨层图谱只在 monorepo 内工作。专家面板收敛:先用 **operationId 契约桥**(前端本就靠后端 OpenAPI 生成,operationId 是现成稳定跨仓主键)把"前端调哪个后端接口"做对 —— 它 repo 无关,当下修 bug、未来多仓零返工;重型多根索引按 validate-first 冻结到真有项目卡住。

## 关联

- 触发来源:2026-06-10 4 视角专家面板讨论(本会话)。
- 主线迭代:[`../roadmap-2026-06-07/`](../roadmap-2026-06-07/)(代码智能平台,本目录独立不继承)。
