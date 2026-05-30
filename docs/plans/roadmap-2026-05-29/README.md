# roadmap-2026-05-29 — 工具栈迭代规划目录

> 本目录 = 2026-05-29 这轮规划的 plan / design 真值源。

## 本轮主题

agent memory 基础设施 —— 身份 + 权限 + 多作用域底座(业务/基建双轮驱动,数据积累期投基建)。

## 文件清单

| 文件 | 内容 | 状态 |
|---|---|---|
| [memory-permission-model-2026-05-29.md](memory-permission-model-2026-05-29.md) | memory 权限模型:两正交轴 + 作用域阶梯 + scoped RBAC + 召回/冲突/生命周期技术方案 + M0-M6 分阶段 | ✅ M1-M4 落地 |
| [mcp-service-ification-2026-05-30.md](mcp-service-ification-2026-05-30.md) | 业务↔平台 MCP 脱文件路径走服务地址(Streamable HTTP/SSE + mcp-proxy 桥,不手搓);chroma/codegraph/cross-link SSE 化 + 多租户 + 分阶段 | ✅ P0-P5 落地 + 双审计 |
| [dual-instance-codeindex-2026-05-30.md](dual-instance-codeindex-2026-05-30.md) | 双实例代码智能:本地实例(working tree, 服务活跃编辑)+ 平台基线实例(GitLab HEAD, webhook 驱动);四触发器 + 用户可切源 + 单写者纪律;无服务器时 WSL2 当平台替身 + 8GB GPU 编排约束 | 📋 待拍板 |

## 关联

- 上轮:`../roadmap-2026-05-28/`(agent P0 骨架 + 跨平台 CLI + 平台翻正)
- 承接:`../roadmap-2026-05-28/agent-2026-05-28.md`(P2/P3 承载 memory 演进)
- 上层规则:`rules/file-discipline.md §4.4`
- 月决策聚合:`docs/log/2026-05.md`
