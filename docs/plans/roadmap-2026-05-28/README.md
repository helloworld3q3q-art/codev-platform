# roadmap-2026-05-28 — 工具栈迭代规划目录

> 工具栈侧 plan 按日期归档(参照业务侧 `platform/docs/architecture/roadmap-*/` 结构)。
> 本目录 = 2026-05-28 这轮平台改造的 plan / design 真值源。

## 本轮主题

平台所有权翻正 + 可观测性 + 通用化 + 可移植化 + 跨平台 CLI + agent 起步 + cross-link 扫描框架 + daemon SRE。

## 文件清单

| 文件 | 内容 | 状态 |
|---|---|---|
| [platform-ownership-inversion-2026-05-28.md](platform-ownership-inversion-2026-05-28.md) | venv/data/launcher 从业务仓翻正归 codev-platform | ✅ 已落地 |
| [xplatform-cli-2026-05-28.md](xplatform-cli-2026-05-28.md) | .ps1 工具链 → 跨平台 Python CLI(`codev_platform.ops`)| ✅ 已落地(未 push)|
| [cross-link-scanner-framework-2026-05-28.md](cross-link-scanner-framework-2026-05-28.md) | cross-link 扫描器框架 | 📋 计划 |
| [daemon-sre-phase2-2026-05-28.md](daemon-sre-phase2-2026-05-28.md) | daemon SRE Phase 2 | 📋 计划 |
| [agent-2026-05-28.md](agent-2026-05-28.md) | agent 开发 plan | 🟢 P0/P1/P1.5/P2 已落地实测(DeepSeek + X-Project-Id 路由);widget 接入 + 多租户全链路通;P3 流式/PG 持久化待做 |

## 关联

- 上层规则:`rules/file-discipline.md §4.4`(本目录结构的强制规定)
- 月决策聚合:`docs/log/2026-05.md`
