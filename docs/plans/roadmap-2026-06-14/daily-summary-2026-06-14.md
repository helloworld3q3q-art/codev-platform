# daily-summary 2026-06-14 —— 平台产品化迭代 Day 1:onboard / 治理 / token

> roadmap-2026-06-14 产品化迭代启动日。代码智能平台到平台期后转**产品化**(从"自用闭环"→"可分发产品")。
> **贯穿主线:每个 P 项代码核实都发现"大部分已存在",真活是补缺口/更新** —— 守住不堆码不重造(用户反复强调的纪律)。
> commit 链:`76d5ea8`(P1)→`ac0126d`(P4)→`379f42c`(P2 token_issue 基础)。全程 origin/dev,相关单测全绿。

## 一、P1 onboard 接入简化 ✅(`76d5ea8`)

痛点:加项目 ~8-10 步手动命令。**核实发现 `ops/onboard.py` 命令已存在**(config/project.json/meta/RBAC/codegraph/reindex 全有,分层关键步停/软步 warn)→ 真活=补 2 软步:
- `sync.py` 抽 repo-aware 公共核心 `sync_resources_to`/`_apply_grep_hook`(从 cmd_sync_hooks 抽去重),`cmd_sync_hooks` 改用 —— onboard(`--repo`)与 cmd_sync_*(cwd)共用不复制。
- `mcp_serve.build_mcp_servers`(复用 `mcp_source_url`)→ onboard 写初始 `.mcp.json`,mcp-source 切源改已存在的,同一 url 派生不漂移。
- onboard 插 `[5/8]` sync + `[7/8]` .mcp.json 两软步(失败 warn 不阻断、已存在不覆盖)。

**WSL 真机验证**:sync 真拷 9 rules/3 skills/1 hook + settings grep merge;`.mcp.json` 4 套真平台端点(19083/19087/19091/19092)。9 onboard 测试 + 98 回归。

## 二、P4 Phase 10 治理 ✅(`ac0126d`)

三子项**又是"大部分已存在"**:
- **治理看板**:`web-ui/src/pages/dashboard/index.tsx` 已组合 IndexFreshness(P1)/GraphHealth(P3)/SoftQuality/TokenUsage(P8)/McpUsage + 平台健康 + 图谱统计 **5+ 卡片** → 已完整,不重造。
- **能力矩阵 enabled**:`describe_capabilities` 是**语言栈插件(默认全开)**,加 enabled 恒 True = **伪需求**,不做(CLI `plugins list` 已展示)。
- **接入指南**:`docs/onboarding-new-project.md` 已存在但 **stale**(讲老手动 8 步 + stdio .mcp.json)→ **重写成一键 onboard + SSE 服务模式**(配套 P1)= 真活。

## 三、P2 多组织 web 签发 UI(起步,`379f42c`)

两兄弟核实 + 安全评审:**org/用户/改密/角色 web 化已全有**(orgs.py+users.py+pages/{orgs,users},且已接 `_guard_home_org`/`_guard_org_member` 安全闸);项目↔org 绑定也有。**唯一真缺口 = token 签发 web 化**(现仅 CLI `gateway pg-token-add`)。范围从"中"收窄到"轻:1 路由 + 1 token 页 + 抽共享 issue"。

- **第一步交付**:抽 `gateway/token_issue.issue_token` 共享纯函数(生成明文+hash+存),CLI `_cmd_pg_token` 改用之去重 —— web tokens 路由下一步复用同一签发逻辑。3 单测 + 68 gateway 回归。
- **web 路由设计就绪**(复用点+5 安全红线落点定死):`schemas/tokens.py` + `services/token_service.py`(复用 `UserService._guard_org_member` + `issue_token` + `PgTokenStore`)+ `routes/tokens.py`(**org_id=sess.org_id 不由 client**)+ 注册 + 越权单测 + 前端 `pages/tokens` + run api。
- **剩余**:web 路由后端(4 文件+越权单测)+ 前端页(WSL run api)。安全敏感 L4,建议清爽 context 实现。

## 四、贯穿教训:先核实避免重造(本迭代反复印证)

P1(onboard 命令)/ P4(治理看板+接入文档)/ P2(org-user 管理)**三项核实都发现"plan 以为要从零做,实际大部分已存在"** —— 说明平台比 plan 假设的成熟。真活都是"补一个缺口切片"(P1 两软步 / P4 一文档 / P2 一 token 路由),新增代码极少,其余复用/更新/证实伪需求。**MCP-first 定位 + 代码核实**(像 atomic handoff/phantom 那样)避免了三次重造。

## 五、对抗审计 + atomic handoff gc bug 双层根治

收口前派 **3 兄弟对抗审计今天全部改动**(atomic handoff / onboard / token+gate,默认怀疑 + 自跑测试 + 写攻击用例),抓 **3 真 bug + 1 防御**:
- 🔴 **gc rmtree 撞 reader 句柄 → Windows PermissionError 阻断重建**:reader 缓存(`_models._clients`/`code_vector_store._QUERY_CLIENTS`)pointer 切换后不 evict,开着的 sqlite 句柄被 gc 的 rmtree → `WinError 32` → gc 无 try/except → 整体抛 → **code_vec 全量重建崩**。**WSL 真机根本验不出**(POSIX unlink 延迟,开着的文件能 rmtree 成功),只在 Windows 暴露 —— 对抗审计 + Windows 句柄占用攻击脚本抓出的**跨平台隐藏炸弹**。
- 🟡 onboard 步骤编号 1/2/3 仍 `[N/6]`(4-8 是 `[N/8]`)→ 改全 `[N/8]`。
- 🟡 onboard 丢弃 `sync_resources_to` 返回值:源缺失时 stderr 喷 FATAL 但仍报"接入完成 rc=0"+ 建议提交不存在的 rules → 读返回值显式 WARN + footer 条件化。
- 🟢 防御:`issue_token` 加 org_id/user_id 非空校验(越权纵深;web 路由仍须断言 org_id 取 session)。

**gc bug 双层根治**:
- **(a) fail-soft 兜底**(`91383e9`):`gc_builds` rmtree 包 try/except,删不掉的旧 build 留待回收,**绝不阻断重建**(commit 已成功 current 已切)。
- **(b) reader evict 根治**(`82f67e8`):reader 解析到新 build 后 pop 缓存里同 base/builds 下非 current 的旧 client(缓存卫生 + GC 释放机会)。**诚实边界**:chromadb `PersistentClient` 共享 System 单例(SharedSystemClient 类级缓存),无干净 per-client close(`clear_system_cache` 全局会断 current)→ pop 不保证立即释放句柄,完全释放仍靠 daemon 重启。三层组合(evict + gc fail-soft + 重启)= chromadb 限制下最优,不硬碰全局风险。

**验证**:41+265 回归 + **WSL 真机**(evict 真 chromadb 实测 `_clients` 切 build 后清旧 → `A_evicted=True / B_present=True`)+ 重启 3 服务 active。**token+gate 审计核验 CLI 改用 `issue_token` 逐字段等价,无致命 bug**。

**教训沉淀**:① 对抗审计抓出 WSL 验不出的跨平台炸弹(POSIX unlink 延迟 vs Windows 句柄锁)—— 真机验证有平台盲区,Windows 边界必须对抗+实测攻击脚本;② chromadb 共享 System 单例让 handoff 场景 reader 句柄释放有硬限制,诚实分 (a) 兜底 + (b) best-effort,不假装能干净 close。

## commit 链(产品化迭代段)
`76d5ea8`(P1 onboard 补 sync+.mcp.json + 抽公共核心)→`71af7f8`(P1 plan 标交付)→`ac0126d`(P4 接入指南重写)→`379f42c`(P2 token_issue 共享)→`91383e9`(审计修 3 bug + gc fail-soft)→`82f67e8`(gc 根治 b: reader evict)。

## web 端
P1/P4/P2 起步均未改 web schema(onboard/治理文档/token_issue 都后端/文档)→ `pnpm run api` 暂不用跑。**P2 web 路由(下一步)改 schema 后须**:重启 codev-web → `pnpm run api` → 前端 pages/tokens(authorized-web-schema-runapi-chain)。
