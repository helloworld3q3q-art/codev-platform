# daily-summary 2026-06-12 —— 多人服务器化:auth 临界路径 + onboarding 闭环

> 承 [`daily-summary-2026-06-11.md`](daily-summary-2026-06-11.md) §十三/十四(多机 arc:PgJobQueue + graph→PG Stage A/B 已审已验)。
> 本日用户明确**最终目的 = 整个平台部署在服务器供多人多机连用,WSL 只是模拟服务器** → 平台开发聚焦"server 化 + 多人 onboarding"。
> commit 链 `de801ea`→`8f95ff7`(全程 origin/dev,WSL 全量 1588→1597 passed)。

## 一、openclaw "串" 诊断 —— Windows 陈旧影子,非平台缺口
用户给的另一 agent 分析把 codev-platform(基础设施)与 openclaw-stock(量化业务租户)**串了**。诊断三因:① 历史纠缠(codev 从 openclaw 仓抽离,docs 互引)② **本机(Windows)openclaw 图谱空**(0 节点,陈旧影子)→ agent 在 Windows 上下文问 openclaw 只能回退读二手文档 ③ 登记 `repo_path="."` stale。**核查 WSL(真 ops 环境)openclaw 健康**:graph **2335 节点/4624 边/当日 15:42 刚 ingest**,codegraph/code_vec/chroma 都在。→ **隔离没破(无跨租户泄漏),是"读不到自己回退到错"**。真分析须在 openclaw 仓(C:\workspace\project)上下文跑(用户自做业务,平台开发归我)。

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

## 九、下午段:roadmap-2026-06-10 两 plan Phase 1 + 对抗审计 + token 端到端验证
承用户"1/2/3 挨着做"。三块独立改动各自 commit + WSL 全量验证(1631→1636 passed),工程化守"语言/仓库无关 + 策略模式 + 单一聚合 + 超行数拆分":
- **Item1 多组织 Phase 1**(`2a6ac3c`):`mcp.bind_host` 配置化 + `startup_policy_error` 聚合三闸(multi_user/deploy/**bind**)接 graph/codegraph/memory 三 run_http,非 loopback bind 启动硬拒。
- **Item2 PG token 闭合脱节洞**(`0be3d21`/`793e971`):`agent_tokens` PG 表(alembic 0006)+ `PgTokenStore.lookup` join users/orgs.status 实时校验 → web 禁用即失效;`TokenAuthenticator` 策略化(`TokenResolver` + Mapping/Pg/Composite,向后兼容 dict);`gateway pg-token-*` CLI。
- **Item3 多仓 operationId 契约桥**(`85857e5`/`71ea071`):`_link.py` 重写——operationId 精确桥 + URL 多服务消歧(修 `candidates[0]` 静默串台);spring/fastapi operation_id meta;`find_contract_drift`(graph MCP 第14工具 + agent 工具,真机抓 openclaw 1 处悬空 `/v1/config/system-status/summary`)。impact.py 超 600 行拆出 `contract_drift.py`。
- **对抗审计**(`d2874ce`):派 3 个 general-purpose agent 默认怀疑 + 自跑攻击用例。抓 4 真漏洞全修 + 测试:R1 org 禁用不失效(lookup 加 join orgs)/ P1 `bind_host=""` 绕闸绑 0.0.0.0(空串回落 loopback + 闸拒空串)/ V1 operationId 碰撞 conf=1.0 错连(降 0.5 让 certain_only 滤)/ V2 `meta=None` 崩(防御 or {})。审计也证真:跨项目隔离红线守住(`load_graph` 上游按 pid 过滤)、token 过期 authenticator 拦、明文不落库、auth_mode 拼错 fail-closed。R2/R5 config 兜底 break-glass → 启动告警 + 文档红线。
- **token 模式端到端验证**:① in-process 真 PG 9/9(身份/授权/越权/401/`?token=`/**禁用用户·禁用 org 即失效**);② live HTTP 真 uvicorn 6 状态码全对(throwaway 端口, 运行 4 守护零干扰, config 用完即恢复)。runbook 标注 `[x]`。**剩**:真跨网络第二台机客户端(周末接入验)。
- commit 链下午段:`2a6ac3c`→`0be3d21`→`793e971`→`85857e5`→`71ea071`→`d2874ce`→`1d0c084`→`0baf67e`(+ roadmap/runbook docs)。
- **隔离泄漏修复**(此前):agent chat 跨项目读文档根因 = 业务仓 `index.json` external_doc_paths 灌平台 docs 进 openclaw collection(非路由串台),已删 + 重建 chroma + 真机 e2e CLEAN(见记忆 [[agent-project-isolation-chroma-leak]])。

## 十、晚间段:一个"加列影响面 agent 答不出"挖出平台一串底层坑(全程逐层证伪)

起因:用户给的 sample-project-beta agent 对「入库订单管理加列影响哪些模块 / 哪些 PDA 页调 `pda/task/check/container`」答不出(撞 max_steps、recall 全 `0.0164` floor)。顺藤摸瓜挖出平台服务**大型复杂多仓项目**时一连串真坑,逐个证伪定位 + 治本。**教训:不要凭现象调参,逐层量化证伪到根因**。

### 10.1 chroma 多 collection compaction 损坏**复发** → 治本每项目独立库(`61759a6`)
- 现象:给 sample-project-beta 索引 311 个 `.rule` 后整库 `database disk image is malformed`,search_docs 全项目下线。
- **证伪"单 upsert 够"**(06-05 的缓解):codev 单 collection 重建干净,**一加 openclaw 第二个 collection 立刻又坏** → 确认多 collection 共库无解(第二个 collection 的 compaction backfill 写坏整库)。
- 治本:`platform_docs` 改**每项目独立库** `data/chroma/docs/<pid>/`(每库一 collection,永不跨 collection compaction),与 `code_vec/<pid>` 同构。`_get_client(project_id)` per-project client 字典;`.reindex.lock` 仍在根做全局 GPU 串行化。daemon **永不开根库** → 一项目 `.rule` 出问题炸不到别项目。3 库 1974/3911/1713 integrity ok。更新记忆 [[chromadb-multiflush-compaction]]。
- 善后:清取证副本 + 损坏根库释放 ~361M;chroma 文本类型支持已扩(html 剥标签 + txt/`.rule`,`doc_patterns` 决定类型)。

### 10.2 codegraph 图谱"发丝团" → 边数按节点比例收口(`c074cda`)
- 现象:web `/codegraph/graph` 对 sample-project-beta(28万节点)返回 2000 节点 / 11106 边 = **5.6 边/节点**,3D 一团乱(openclaw 2.9 / codev 2.3 正常)。
- 根因:edge-first 选边(修"散点"加的)对超大稠密项目过度,默认 edge_cap=50000。
- 修法:`_EDGE_PER_NODE_CAP=3`,`edge_cap=min(edge_cap, node_cap*3)`。只压过密项目(ideas → ≤6000 边),稀疏的不动。

### 10.3 token 模式下 `/embed` 401 → 纯算力接口本机 loopback 免 token(`a6b8093`)
- 现象:切 token 模式后,内部 code_vec 索引 / agent-memory 写经 `RemoteEmbedder` 调 daemon `/embed` 不带 token → 401 → **向量 lane 全挂**(recall 退化关键词,分全 floor)。
- 修法:不把 `/embed` 设 public(远程白嫖 GPU),而是 `AuthMiddleware.loopback_exempt_paths`:`/embed`/`/rerank` 无租户数据,**仅真实 TCP 对端 loopback** 免 token,远程仍鉴权。对端取 `scope["client"]` 不信任 X-Forwarded-For。

### 10.4 code_vec 索引性能/稳定**连环坑**(大项目服务的硬骨头)
1. 逐节点一次 HTTP embed 太慢 → **批量** encode(`2b9cac6`)。
2. 批量 256/批撞 30s HTTP 超时 + manifest 只末尾写(中途失败丢全部进度)→ **64/批 + 自适应超时 + manifest 增量 checkpoint**(断点续)(`14114f1`)。
3. **daemon `/embed` 死锁**(根因级):`async with gpu_sem: await to_thread(encode)` —— 一次 encode 卡住则串行 GPU 信号量**永不释放**,`/embed` 整体死锁连带打挂在线 search_docs(重启才恢复)。→ `_gpu_call` 包 `wait_for(GPU_OP_TIMEOUT=120s)`,超时**释放信号量**返 503(`870a216`)。**上云硬前提 + 线上稳定性炸弹**。
4. 本机 vs 远程 embedder **配置缝**(`294a487`→默认翻回 remote `1cae618`):8GB 单卡塞两份模型(daemon 2.5G + build 自己一份)挤爆显存 build 卡死在 65000;**云上无 GPU 的 worker 只能 remote** → `recall.code_vec.embed_backend` 默认 **remote**,`qwen-local` 为专用 GPU 索引节点 opt-in。
5. PyTorch CUDA 缓存分配器**膨胀到 7.7/8GB** → 新 encode `AcceleratorError`(OOM)→ 大 batch(≥16,索引侧)后 `torch.cuda.empty_cache()` 去碎片(`63b80df`)。
6. **固定 1500 字符硬切**把方法切一半(实测 6.1% 节点被截,7492 方法切半,god-class 75万字符只嵌 0.2%)→ **kind 感知切割 + 长方法滑窗**(`0fa1730`):class 只嵌头部摘要(body 由成员方法节点覆盖),method 完整体超 3500 行边界滑窗(重叠 8 行,≤12 块),多块 `node_id#k` 召回去重回节点。真数据验证放大仅 1.00x、最大块 3901、0.1% 切多块。

### 10.5 graph url_registry 链路 method 未知误判 mismatch → 纯 URL 匹配(`07c856e`)
- 现象:PDA `PICK_CHECK_TURN = SUFFIX+'task/check/container'` 明明调了后端 `GET /pda/task/check/container`,但 `api_callers` 像没连。
- 根因:url 常量**本身没有 HTTP 动词**(动词在调用点 `.get()/.post()`),`url_registry` 扫描器默认 POST → `_link` 看 POST≠GET 把真链路打成 `conf=0.7 + 误标 method_mismatch`。**HTTP 方法不止 GET/POST**,凡真实动词非 POST(GET/PUT/DELETE/PATCH)的 url_registry 链路全中招。
- 修法:`meta.url_registry=True` = method 未知 → 不按假 method 过滤/惩罚,纯按 URL 匹配,唯一候选 conf=1.0(`url_only`);同 URL 多动词仍降候选。
- 附带纠错:别的 agent 把"这接口没连上"误判成"PDA 前端不在本仓"——PDA 前端**在图谱里**(646 frontend_api_call 等),是这一条链路的 method 坑。

### 10.6 运维/工具坑(记着别再踩)
- **WSL `origin` 是自建 Gitea**(`internal.example.invalid:3000`/`origin`)**不是 github**:Windows 改完 `git push origin dev`,只 push github 则 WSL `git pull` 拉不到 → 旧码 worker 用旧逻辑跑(踩过一次,及时停 worker)。见 [[gitea-ci-runner-wsl]]。
- **Bash 工具够不到 `wsl.exe`**(`/mnt/c/Windows/System32/wsl.exe: No such file`)→ 后台脚本静默没跑(echo 照常误导)。**WSL 操作一律走 PowerShell 工具**;复杂命令写 `.sh` 文件再 `wsl bash 文件`(避免 PowerShell 吃 `$!`/`$()`/嵌套引号)。补记忆 [[wsl-run-commands-from-windows]]。
- **daemon mid-build 重启**会造 GPU 争用尖峰(daemon warmup + build 两份模型撞 8GB),build 进度假性停滞 —— 大索引期间别重启 daemon;真要独占 GPU 跑大索引就**停 daemon + qwen-local opt-in**(本会话最终用此法本地建)。

### 10.7 commit 链(晚间段)
`61759a6`(chroma 每项目库)→`c074cda`(图密度)→`a6b8093`(/embed loopback 免 token)→`2b9cac6`(批量 embed)→`14114f1`(超时+checkpoint)→`870a216`(daemon wait_for 死锁)→`294a487`→`1cae618`(embedder 本机/远程缝,默认 remote)→`0fa1730`(kind 感知切割+滑窗)→`07c856e`(url_registry method 纯 URL)→`63b80df`(empty_cache 防膨胀 OOM)。

### 10.8 结论
这条链是平台从"够用"走向"**服务大型复杂多仓项目**"必踩的工程化硬骨头:存储隔离(每项目库)、共享 GPU 服务的死锁/膨胀/鉴权、大批量索引的批量/断点/切割、跨仓链接的 method 语义。全部逐层证伪定位 + 治本 + 断言测试 + 真数据验证。code_vec 新切割全量重建 + graph re-ingest(让 method 修复生效)收尾中。
