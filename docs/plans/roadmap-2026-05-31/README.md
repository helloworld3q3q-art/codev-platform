# roadmap-2026-05-31 — 工具栈迭代规划目录

> 本目录 = 2026-05-31 这轮规划的 plan / design 真值源。

## 本轮主题

**多组织服务器加固** —— 平台从"单人能跑"推向"多组织/多人/多项目可安全共享"。核心是补齐多租户**授权隔离**(项目 ACL),其余按"上线必须 / 健壮性 / 体验"分级排期。

## 文件清单

| 文件 | 内容 | 状态 |
|---|---|---|
| [multi-org-server-hardening-2026-05-31.md](multi-org-server-hardening-2026-05-31.md) | 全量现状盘点(A 安全/B 可靠/C 运维/D 体验/E agent/F 索引)+ 优先级路线 + **ACL 项目隔离(模型 C)详细设计** + 后续阶段 backlog | 🚧 ACL 开发中 |

## 关联

- 上轮:`../roadmap-2026-05-29/`(agent memory 基建:身份 + 权限 + 多作用域,M1-M4 落地)
- 承接:`../roadmap-2026-05-29/memory-permission-model-2026-05-29.md`(memory M5 ACL 接缝;本轮项目 ACL 与其 §3.4 同源对齐)
- 上层规则:`rules/file-discipline.md §4.4` / `rules/agent-provider-architecture.md`(策略接口+registry+零 if-else 铁律)
- 月决策聚合:`docs/log/2026-05.md`
