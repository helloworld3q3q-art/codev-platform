# daily-summary 2026-06-09 —— Phase 1/3 落地 + 前端孤岛修复 + 可观测性 + 门禁

> 承 [`daily-summary-2026-06-08.md`](daily-summary-2026-06-08.md)(Phase 7 planner 每模型策略 + A/B + impact 调参)。
> 本日把 roadmap-2026-06-07 蓝图又往前推 1/3/10 三块, 并修了一个前端跨层断裂真 bug。

## 一、Phase 1 IndexManifest(端到端闭环)

- `index_manifest.py`: 统一 `index_builds` 表(每 project×kind 最近构建 commit/耗时/状态), reindex worker 终态 best-effort 写一行(不阻断索引)。
- CLI `index status` + freshness(记录 commit vs 仓库 HEAD → 对齐/落后/未知)。
- **Web 端点** `POST /api/v1/indexes/status` + **dashboard 索引新鲜度卡片** —— Gate "Web 暴露 freshness" 补齐。
- 真机:worker 写真行 `codegraph 对齐 ✓`;端点 live 注册;7 单测。

## 二、Phase 3 图谱审计 + pre-push 门禁

- `graph/audit.py`: 结构审计 errors(断链/跨租户串台/**孤儿 plugin**)+ warnings(重复/低置信)。
- **孤儿 plugin 检测**抓出真 bug: A2 软节点曾被 `arch_layer` 自名 plugin 直 upsert 残留(reindex 只清规范 `builtin.analyzers` → 永不清), 致 11 个角色节点重复。**已 purge + 加检测防复发**。
- `graph audit --all` + `tools/dev/pre-push-audit.ps1`: **接进 git pre-push**, 结构 error 非零退出挡 push(无 store 优雅跳过, 只读 sqlite 内存友好)。多次真机自跑 `OK clean`。
- **Web 端点** `POST /api/v1/graph/audit` + **dashboard 图谱结构健康卡片**。8+ 单测。

## 三、前端孤岛 bridge linker(修真 bug, 通用)

- 两个前端插件(frontend_deps 建 module / react 建 api_call/route)为同批文件建节点但 id 不相交、无边相连 → `frontend_module` 成孤岛, impact 滤软边后**到不了后端**, 前端页→接口→表 跨层链断。
- web 上 codev-platform "看着连"是 **A1/A2 软边经角色 hub 糊的视觉假象**(连通块: 硬骨架 32 块 vs 含软边 1 块);量化仓无软层直接裸露孤岛。
- 修: ingest `_frontend_bridge_pass` 按文件 `module --contains--> 同文件 api_call/route`(硬边)。**通用对所有项目生效**。真机: openclaw-stock `find_page_dependencies(alerts页) → backend 51 / database 194`, 跨层链贯通。

## 四、web 软硬边区分

`Graph3DCanvas` 加 `linkIsSoftFn`: 软边(plays_role/belongs_to_domain)淡色细线退背景, 硬依赖实线 —— 让"经角色 hub 连通"不被误读成功能依赖。按 kind 判定, 无需 pnpm run api。

## 五、可观测性收口

dashboard 现有 **索引新鲜度(Phase 1)+ 图谱结构健康(Phase 3)** 两张卡, "索引新不新鲜 / 图谱结构干不干净"一眼可见。

## 六、教训: 内存危机(记一笔)

32G 机器跑爆: WSL2 默认吃一半(16G)+ 浏览器 3D 统一图谱占核显共享内存(11G, 从系统 RAM 挖)+ 超长会话终端 scrollback(37G commit)。处置: `wsl --shutdown` + **`.wslconfig` 封顶 WSL 12G + autoMemoryReclaim** + 关 3D 图谱页 + 重启。**结论: 3D 大图谱用完即关; 超长会话适时开新终端。**

## 七、roadmap 进度

落地 ~30-35%: Phase 0/7 实质推进, Phase 1/3 本轮做成 MVP+Web+dashboard, Phase 4/6/10 轻量覆盖。重型 Phase 2(统一 IR)/5(path scoring)/8(性能)/9(多语言 adapter)按 §六纪律待真实需求触发。

## 八、统一图谱完整重建验证(走 worker, 非手动)

对两仓**经 reindex worker 正经重建 + 审计**, 验证本轮修复经住干净重建:

| 仓 | 重建后关键产物 | 审计 |
|---|---|---|
| codev-platform | 498 节点; `contains` 76(bridge)/ arch_layer 11 + plays_role 471(A2)/ business_domain 12 + belongs_to_domain 121(A1) | **clean, 0 error** |
| **openclaw-stock(量化)** | `contains` **214**(bridge, 前端孤岛已连)/ arch_layer 9 + plays_role 2263(A2)/ **business_domain 27 + belongs_to_domain 219(A1; 之前 0 软层, 本次产出)** | **clean, 0 error** |

两仓均: 断链/串台/孤儿 plugin/重复 **全 0**。三修复(frontend_bridge / arch_layer 孤儿不复发 / A1·A2 软层)全部经住干净重建; **量化仓顺带补齐了之前缺的综合理解层**(A1 业务域 + A2 架构层, deepseek 真跑)。

**教训(已记)**: 手动 `graph ingest` 在裸 shell 跑会撞 **npx dependency-cruiser 冷启动** → fail-soft 返 0 退化 + upsert 覆盖好数据(frontend_deps/calls/analyzers 全 0)。**重建一律走 reindex worker**(`reindex-queue enqueue <pid> --kind ingest`)—— 它在 systemd service 环境有正确的 node/codegraph, 不会冷启动失败。

## commit 链(本日)
`6d07da3`(Phase1 manifest)→ `e85d582`/`4928dca`(Phase3 audit+孤儿检测)→ `faacb0b`(前端 bridge)→ `745d3e4`(web 软硬边)→ `1f492d3`(pre-push 门禁)→ `faef6cd`/`e8087aa`(index status web+卡片)→ `01af953`/`0ea0c5b`(graph audit web+卡片)。
