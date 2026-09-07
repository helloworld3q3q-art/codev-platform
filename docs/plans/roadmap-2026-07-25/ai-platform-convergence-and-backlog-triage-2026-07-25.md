# AI 平台收口与遗留任务裁决计划

> **状态：** 进行中（本任务仅完成盘点、裁决与后续顺序；不直接进入实现）
>
> **目标：** 把平台收口为“`git push` 后按改动范围增量更新，AI 可查询已验证最新索引”的可靠内部能力；明确历史 runtime-generation 待办哪些保留、哪些延后，避免无条件继续大型重构。

## 一、范围与边界

### 本计划包含

- 盘点现有 roadmap 中尚未完成的 runtime-generation 任务及其触发条件。
- 根据当前 WSL 运行态、索引链路和实际检索证据，为任务划分“当前必须 / 观察后决定 / 暂缓”。
- 为后续真正的代码或运行态变更建立严格顺序；每项真正开工时另建独立任务计划。

### 本计划不包含

- 不实现认证、打包、索引或运行时改动。
- 不启动 Foundation Task 5 以后、Adapters 或 Integration 的历史任务。
- 不重建 runtime、base、依赖、数据库或索引；不操作 WSL 服务。
- 不提交、不推送，尤其不推送 GitHub。

## 二、已核对的事实基线

| 项目 | 当前事实 | 结论 |
|---|---|---|
| 日常增量链路 | WSL worker 正常空闲，队列 `pending=0`、`active=0`；最近文档提交只更新 `chroma` | 日常 push 不需要全量重建 |
| 四类索引 | `chroma`、`codegraph`、`ingest`、`code_vec` 的 manifest 均为 `ok`，并各自与对应 target/runtime 对齐 | 索引真值已收口 |
| MCP 服务 | platform-docs、codegraph、graph、agent-memory 的服务端探测均为 OK | 服务端存活正常 |
| 实际检索 | 近 7 天记录 101 次查询；codegraph/graph/vector 均有命中 | 核心检索并非空壳 |
| 客户端可用性 | 当前 `health --all` 与本会话 MCP 初始化仍出现 Bearer Token 401 | 服务存活不等于 AI 客户端可用，必须优先收口 |
| 多项目目录 | 版本化 WSL runtime 的 `list-projects` 未能定位项目登记表 | 多项目产品能力尚未完全闭环 |

> 索引是 Git 源码和文档的可重建派生数据，不是业务主数据。`agent-memory` 由 Agent/对话写入，不属于 Git push 的四类索引更新。

## 三、历史待办的裁决

### 1. 已完成且不得重做

- `roadmap-2026-07-08` 的 reindex worker 生命周期任务。
- `roadmap-2026-07-09` 的 reindex-worker-v2 Task 1–12。
- `roadmap-2026-07-19` 专项 Task 1–7，以及本次 WSL 四类 manifest 收口。

这些任务只保留为审计证据；日常 push 继续走已有串行增量队列，禁止以它们为理由重新建库、重装依赖或新建第二套运行环境。

### 2. 当前不启动、保留为条件触发的 25 项

| 系列 | 数量 | 当前裁决 | 仅在以下条件出现时启动 |
|---|---:|---|---|
| Foundation Task 5–7 | 3 | 暂缓 | 需要真实 A/B 独立 release 往返、跨版本回滚或首次 legacy 接管 |
| Adapters Task 1–9 | 9 | 暂缓 | 需要在线数据库结构演进、代际索引原子切换、systemd bundle 事务或 serve-permit |
| Integration/WSL Task 1–13 | 13 | 暂缓 | 上述代际能力已被批准实施，且必须做真实多 revision 部署/回滚/GC 验收 |

这些任务不是“作废”，而是面向未来代际发布、双版本兼容与复杂回滚的工程。当前“单一 WSL 环境、日常 push 增量更新”的目标已经不依赖它们。未经用户再次明确批准，不得以“顺手完成计划”进入 Task 8 或继续该 25 项。

### 3. 当前真正应优先处理的收口项

| 优先级 | 独立后续任务 | 交付边界 | 完成判据 |
|---|---|---|---|
| P0 | MCP 客户端认证链路 | 已由 [`../roadmap-2026-07-28/mcp-client-auth-closure-2026-07-28.md`](../roadmap-2026-07-28/mcp-client-auth-closure-2026-07-28.md) 完成 Windows 用户级 token 对齐与四端点协议验证；不降低认证要求、不输出 token | 当前 Codex 会话重启后完成最终客户端验收 |
| P0 | WSL runtime 项目登记表定位 | 只修复运行时资源打包或配置路径，不改业务项目元数据 | `list-projects` 在服务运行身份下能列出已注册项目 |
| P1 | 单一增量状态视图 | 聚合 commit、scope、队列和四类 manifest；只读、无新数据库真值 | 一条命令可说明“本次 push 是否已完成、为何只更新部分索引” |
| P1 | 检索质量与路由评估 | 用真实 trace 评估 code_vec 的低命中原因；不先删库、不先重写架构 | 能以数据决定保留、降级为 fallback 或优化 code_vec |

## 四、执行顺序

1. **先建立 P0 认证任务计划并完成实际 MCP 调用验收。** 未解决 401 前，不扩展 Agent、Web、RBAC 或新的检索能力。
2. **再建立 P0 项目登记表任务计划。** 让多项目这一承诺在版本化 WSL runtime 中可用。
3. **建立 P1 可观测性任务计划。** 日常只看“推送提交 → 影响 scope → worker → manifest”的单一事实链。
4. **在有稳定真实查询样本后评估 code_vec。** 若无法证明它改善代码检索，则改为按需 fallback；不因已经投入而强行保留。
5. **只有业务明确要求跨版本发布/回滚或在线迁移时，才从原 runtime-generation 计划按依赖顺序恢复对应任务。** 恢复时更新原任务计划，不复制历史复选框。

## 五、长期边界

- 核心保留：project_id 隔离、增量队列、四类派生索引、MCP 查询端点、manifest/health 证明。
- 暂停扩张：新的 Agent loop、Web 管理面、组织/RBAC 产品化、额外语言插件、复杂代际发布能力。
- 日常变更一律最小 scope：文档只更新 `chroma`；代码改动才按依赖更新 `codegraph`、`ingest`、`code_vec`；不得把 push 当成全量重建信号。
- 任何后续修复都遵守单一职责、低耦合、模块化和可测试边界；避免把认证、索引、发布和运行时逻辑重新耦合到单一大模块。

## 六、验证与完成条件

本治理计划本身完成时：

```powershell
git diff --check
Get-ChildItem docs -File | Where-Object Name -ne 'README.md'
Get-ChildItem docs\plans -File | Where-Object Name -ne 'README.md'
git status --short
```

后续实现不复用本计划作为实现许可：每个 P0/P1 项需先在对应 roadmap 创建独立计划、登记 README，并按其影响面做最小验证。

## 七、当前状态与下一步

- [x] 核对当前 WSL 队列、四类 manifest 和 MCP 服务端状态。
- [x] 盘点旧 `.superpowers` 迁移记录与当前 roadmap；当前无 `.superpowers` / `docs/superpowers` 任务真值文件。
- [x] 对历史 25 项做条件化裁决，不进入 Task 8。
- [x] 用户已确认并完成 P0“认证链路收口”实施；当前 Codex 会话重启后做最终 MCP 工具验收。

## 八、首次盘点验证记录（2026-07-25）

- `git diff --check` 通过；新 roadmap 目录中的计划与 README 均存在，`docs/plans` 根目录无散落计划文件。
- 本任务开始前工作树为空；当前仅新增本目录两个未提交计划文件，未修改既有源码或运行态。
- 发现 `docs/` 顶层已有 14 个历史散落文档，违反当前目录归类规则；它们在本任务前已存在且与本次平台收口无关，未擅自移动或删除。若要治理，必须另建独立迁移计划并核对链接与文档索引。
