# daily-summary 2026-06-12 —— 多人服务器化:auth 临界路径 + onboarding 闭环

> 承 [`daily-summary-2026-06-11.md`](daily-summary-2026-06-11.md) §十三/十四(多机 arc:PgJobQueue + graph→PG Stage A/B 已审已验)。
> 本日用户明确**最终目的 = 整个平台部署在服务器供多人多机连用,WSL 只是模拟服务器** → 平台开发聚焦"server 化 + 多人 onboarding"。
> commit 链 `de801ea`→`8f95ff7`(全程 fuwuqi/dev,WSL 全量 1588→1597 passed)。

## 一、openclaw "串" 诊断 —— Windows 陈旧影子,非平台缺口
用户给的另一 agent 分析把 codev-platform(基础设施)与 openclaw-stock(量化业务租户)**串了**。诊断三因:① 历史纠缠(codev 从 openclaw 仓抽离,docs 互引)② **本机(Windows)openclaw 图谱空**(0 节点,陈旧影子)→ agent 在 Windows 上下文问 openclaw 只能回退读二手文档 ③ 登记 `repo_path="."` stale。**核查 WSL(真 ops 环境)openclaw 健康**:graph **2335 节点/4624 边/当日 15:42 刚 ingest**,codegraph/code_vec/chroma 都在。→ **隔离没破(无跨租户泄漏),是"读不到自己回退到错"**。真分析须在 openclaw 仓(D:\WorkSpace\platform)上下文跑(用户自做业务,平台开发归我)。

## 二、重排优先级:多人服务器 blocker = auth 不是 Stage C
核查 WSL config `auth_mode` 未设 = **passthrough(多人零隔离,所有人同一 advisory-allow 身份)**。auth 代码**全建好**(TokenAuthenticator Bearer sha256 / AuthMiddleware 纯 ASGI SSE 安全 / 账号 org 成员 API / bootstrap admin / pbkdf2 / session mem+PG / `gateway token-add` / `client-auth` / fail-fast 多人+passthrough 拒启动)。**切 token 模式即激活已测的 org/user/project 隔离**。→ Stage C(graph→PG)是健壮性(并发上来再做),不是多人 blocker。

## 三、唯一真缺口闭合:MCP 客户端经 SSE 带 token(query-token 兜底)
.mcp.json 的 SSE 连接能否带 Authorization header + ${VAR} 展开**取决于客户端**(不确定);但 url 一定能填。
- **服务端**(`de801ea`):authenticator 加 `?token=` query 兜底(header 优先更安全;url 兜底=任何客户端可认证),middleware 传 scope query_string。**改 authenticator 签名漏改 SessionAwareAuthenticator wrapper → middleware 传 query 位置参 TypeError→500**,全量验证抓到并修(回归=宽改动跑全量的价值)。
- **客户端**(`de10714`):`gateway client-auth --query-token` 把 token 明文内联进各 sse url 的 `?token=`(保留原 query/不叠加/--remove 可撤)。
- **端到端验**:真 AuthMiddleware→scope query_string→authenticator→identity 整链测过(?token= 通/错 token 401/无 token 401);+ HTTP 入口结构守护 `test_web_graph_authz`(每端点必挂 project 闸或 admin 闸)+ MCP 入口 `test_graph_mcp_authz`(`authorize_graph_request` 抽共用,跨 org→403)。
- 安全:query token 进 URL/access log,远程务必 TLS + 脱敏;header 优先。

## 四、账号 onboarding CLI —— 现成,避免堆屎山
`#4 用户账号 CLI` 调研发现 **`ops/org.py` 已现成**(`org create/add-user/add-member/project grant`,走 **RbacStore**=`can_access`/`fetch_membership` 真正读的隔离存储)。我误写的 `ops/account.py` 用的是另一个 store(web account_store)= 重复+用错存储,**已删**(不堆屎山)。

## 五、厘清 account_store vs RbacStore 双 store + web 登录密码 CLI(`9a103b6`)
- **判定**:orgs/users/org_members 等 7 表是**单一真值源**(web/db/tables.py),A(account_store)与 B(RbacStore)**同表两访问层、职责互补、非 fork**:**A 管身份+密码**(users.password_hash 只 A 写,web 登录读 A)/ **B 管授权+角色**(org_members/project_access,can_access 读 B)。坑=`org add-user`(B)建行不写密码 → 不能登录(创建协议分叉,非数据 fork)。
- **不造 fork 的密码路径**:password 只一列只 A 写 → `UserStore/PgUserStore.set_password` **定向 UPDATE 该列**(不全量 upsert,保住 B 设的 display_name)+ CLI `org set-password <user> --password`。**MCP/SSE 接入走 token 不需密码**,仅 web 控制台登录用。测:mem 更新+保字段+缺user False + CLI 流程 + PgUserStore 对 sqlite engine 验真 UPDATE SQL。沉淀记忆 [[account-rbac-two-store-model]]。

## 六、多机基础端到端验证(只读,不动线上)
- **工厂 pg 分派**:`CODEV_PLATFORM_GRAPH_BACKEND=pg` → open_store → PgGraphStore 往返(2节点/1边/stats/list_project_ids 全对真 PG)——契约测试是直接 new,这里验"config→工厂选 pg"集成路径。
- 队列多机协调已集成测(06-11 审计:亲和/reclaim/并发认领/P0 接管)。
- 结论:多机存储基础**已建+已审+已端到端验,dormant 就绪**;Stage C(切 pg + sqlite→pg 迁移 + health pg-aware)等真部署第二台再启。

## 七、产物
- runbook [`multi-user-server-deploy-runbook-2026-06-12.md`](multi-user-server-deploy-runbook-2026-06-12.md):WSL-sim→token 模式多人服务器 5 步临界路径(发 token→切模式→org 建账号/set-password/授权→client-auth --query-token→网络+重启)+ 验证 + 待补(Stage C / 真机端到端)。
- 记忆:[[account-rbac-two-store-model]] 新增;[[multi-machine-platform-arc]] 补 server 化 + auth 临界路径 + Stage A/B 完成 + 隔离决策。

## 八、roadmap-2026-06-07 收尾(盘点剩余 + 2 兄弟并行清 4 项)
盘点本 roadmap"还有多少没做":11-Phase 蓝图刻意只交付高 ROI 项,其余 trigger-gated;对照 06-08~12 实际交付,真正"可做但未做"只 4 小项(其余=刻意 trigger-gated / 已证伪收口)。派 2 兄弟并行(后端 ‖ 前端不相交树):
- **前端兄弟**(`967bbdd`):① soft-quality dashboard 卡、② impact-paths 路径可视化 —— 核查**早已存在并接线**(SoftQualityCard 接 postSoftQuality / impact 页 PathsList 接 postImpactPaths),roadmap TODO 系 stale。③ 清 `codegraph/common/services.ts` 二次封装(真活):删 wrapper, 5 消费者直调生成接口(postSearch/postCodegraphNode/postNeighbors/postGraph/postFileTree)+ res.data 不 as, tsc 0 error。只提交 agent 6 文件(不卷入非它改的 loginVideo.mp4)。
- **后端兄弟**(`19e77ef`):loop-cost 残留 —— ① per-租户 token 计量(只读: Trace 加 org/user 取已记录身份不造新通道, usage_report 增 byOrg/byUser + agent_usage_by_tenant; 既有 report+web schema 不动前端无感)② per-档 max_steps cap 机制(LoopPolicy.max_steps_caps 默认 {} = 不改现状, effective_max_steps=min, config 按 provider 档解析, **cap 值 A/B 后再定不盲砍**)。366 agent 测试过, WSL 全量 1606 passed。
- **结论**: roadmap-2026-06-07 **基本做完或刻意不做**;真活只剩 trigger-gated 重型地基(Phase 2 IR/4 社区检测否/8 性能/9 多语言/10 治理)。代码智能层判定到平台期, 主线已转多机/多组织服务器 arc。

## commit 链(2026-06-12 段)
`de801ea`(query-token 服务端+runbook)→`de10714`(client-auth --query-token + 端到端验)→`72365f8`(runbook 用真 CLI)→`9a103b6`(双 store 厘清 + org set-password)→`8f95ff7`(runbook 密码路径)→`8c22eeb`(daily)→`967bbdd`(前端清 services.ts)→`19e77ef`(后端 loop-cost 残留)→`cff384e`(daily §八)。
(承 06-11 段:graph→PG Stage A `0d51903` / Stage B `4c773ef` / 审计修 `679714b` / MCP authz `2f13c56`。)

## web 端
本会话改动**不需前端同步**:只 `0d51903`(graph Stage A)碰 web/routes 且是纯内部重构(端点/schema 零变),其余在 gateway/auth/store/CLI 层 → OpenAPI 未变,`pnpm run api` 不用跑。token 模式下 web-ui 登录走 session token(已建+wrapper 已修)照常。**多人 web 控制台管理(浏览器建用户/设密码)是 net-new 前端缺口**(现走 CLI),要做再挂 user 路由 + users 页表单 + run api。
