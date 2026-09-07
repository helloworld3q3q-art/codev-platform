# roadmap-2026-07-28

> **当前状态：** 本目录的两个 P0 收口项均已完成。
>
> **关联裁决：** [`../roadmap-2026-07-25/`](../roadmap-2026-07-25/) 定义平台收口顺序；本目录只执行其中的第一个 P0，不启动旧 runtime-generation Task 8 或其余 25 项。

| 文件 | 状态 | 说明 |
|---|---|---|
| [mcp-client-auth-closure-2026-07-28.md](mcp-client-auth-closure-2026-07-28.md) | 已完成 | 2026-07-29 新 Codex 会话已实调四个 MCP；继续保持 Bearer 鉴权与 WSL root 私有 env 文件权限 |
| [wsl-runtime-project-registry-closure-2026-07-28.md](wsl-runtime-project-registry-closure-2026-07-28.md) | 已完成 | 已验收 WSL current runtime 在未设置 `CODEV_PLATFORM_META` 时由服务账号列出 7 个项目；未重建依赖、数据库或索引 |
| [daily-thin-runtime-promotion-2026-07-28.md](daily-thin-runtime-promotion-2026-07-28.md) | 已完成 | 候选权限、root `-B` 防护、`platform_meta` 白名单、维护态薄发布门禁、受管配置引导、bridge 来源、目标 release 访问发布链、timer 兼容、MCP 冷启动验收、生产 Git 跟踪刷新、Chroma 瞬态 compaction 删除重试及四类增量索引回执均已完成；未重建数据库、base 或依赖 |
| [openclaw-stock-index-integrity-recovery-2026-07-28.md](openclaw-stock-index-integrity-recovery-2026-07-28.md) | 已完成 | 已发布关闭写端后的独立持久化证明；两项目实数、最新提交、四服务与正常 hook 写入均验收通过，未重建 base、依赖或数据库 |

## 本轮约束

- 保持 Bearer 鉴权，不记录、不轮换、不暴露 token。
- 只做最小认证链路修复；不重建索引、数据库、runtime、base 或依赖。
- 不扩展 Agent、Web、RBAC、项目登记表或历史代际任务；如有新需求，先建立独立计划。
