# daily-summary 2026-06-13 —— PDA 链路从"答不出"到"精确可读全链路" + 挖到部署根因

> 承 [`daily-summary-2026-06-12.md`](daily-summary-2026-06-12.md) §十(一个"加列影响面 agent 答不出"挖出平台一串底层坑,收尾中)。
> 本日把那条线**做完并验证**:code_vec 大项目全量建成、find_api_callers 真因定位、前端调用方精确归因、跨仓 config 版本化、agent 文件工具多仓,最后挖出"改了反复不生效"的**部署根因**。
> commit 链 `712588e`→`796665b`(全程 fuwuqi/dev,相关单测全过;Windows 侧 2 个 psycopg_pool 缺包失败与改动无关)。
> **贯穿教训:不凭现象/直觉下结论,逐层实测证伪到根因 —— 本日两次猜错(URL 没解析 / 非代码文件)都被实测数据纠正。**

## 一、code_vec 8GB 大项目建库收尾(batch + 探活 + 反应式回收 + build_text 封顶)

ideas-v2(codegraph 28.4 万节点 → 入库 19.8 万 chunk)在 8GB 笔记本卡上反复建不完。逐层定位 + 治本:

- **fp32 保精度,batch_size 封顶激活峰值**(`712588e`):用户否掉"多进程并行/CPU 卸载"(前者各背一份模型显存叠加更糟,后者对 0.6B 太慢)。真正可控的是 encode 内部分批 → `models.embed_encode_batch`(默认 8),remote(daemon /embed)与本机 qwen-local 共用同键。`skip_kinds` 配置化(默认 import/file/variable 不变,平台零影响)。
- **/embed 反应式 OOM 回收**(`a0485ef`):撞 GPU OOM → empty_cache 重试一次 → 仍失败返 **503 可重试**(接 RemoteEmbedder 退避 + checkpoint),不把瞬时尖峰升级成 build 失败。与已有"主动每批 empty_cache"互补。
- **增量续跑前探活既有库**(`6530886`):worker 超时是 **SIGKILL**(非优雅停),会把 chroma 库半写坏在 flush 中途;续跑接脏 segment → compaction 522 → malformed 彻底崩(实证:首建超时被杀留脏库,续跑写到 16 万崩)。R5:续跑前读侧 `quick_check` 探活,坏了退全量 rmtree 自愈。
- **build_text 给 signature/docstring 封顶**(`f3704ac`,**timeout 真因**):build 在尾部(vtradex.otwb 遗留 WMS)反复 /embed 超时。**实测否定"非代码文件"猜测**(codegraph 不索引 word/excel/图片);真凶是合法但超大的 **Java 方法 docstring 达 17KB**(巨型块注释),`build_text` 没封顶 → 塞进 base 且**每个滑窗重复带一份** → 单 chunk 24KB ≈ 6000+ token → 成批长序列 encode 超 daemon 120s;且 24KB 塞一个向量严重稀释语义。`_BASE_FIELD_MAX`(signature 600 / docstring 1200)→ 单 chunk ≤ ~5.5KB,encode 飞快。
- **结果**:封顶后增量续跑 ~5 分钟补完,**198856 / 198856 全量建成,0 OOM/0 损坏/0 超时**。`.yml` 配置节点(10276 个)用户决定**保留**(非超时主因)。

## 二、find_api_callers 真因在查询层不在数据(`ba05cf9`)

`pda/task/check/container` 的前端调用方,IDE + web 双 agent 都 `found:false`。**逐层证伪**排除了项目隔离(PDA 仓在 extra_repos)、数据漏扫(PDA 扫了,646 frontend_api_call)、URL 解析(`SUFFIX+片段`拼接正确,meta.url 对)—— **真因是 `_resolve` 只按节点 id(带 `GET:` method 前缀)/ name(handler 名)匹配,不认 `meta.url`**。人/agent 自然用 URL 路径指端点 → 两招都不命中。

- 修:`_resolve_endpoint` 补按 `meta.url` 路径匹配(`_norm_endpoint_path` 剥 method 前缀/query/尾斜杠);同路径多 method 全返回,聚合调用方。旧 id/name/歧义路径不变。+2 测试。

## 三、前端 API 使用精确归因引擎(`6cfa250`)

#2 修好后 find_api_callers 返回**端点对的页**但夹 **97 个噪声页**:`端点 ← 常量 ←(contains)共享 URL.js ←(imports)97 页`,凡 import 注册模块的页都被算调用方。

- **新解析引擎 `_stack_scan/api_usage.py`(策略式)**:`UsageResolver` Protocol + `ConstantReferenceUsageResolver`(url_registry 常量按名引用);驱动**每文件源码只读一次**(跨策略共享 token,O(token) 相交)。加新使用模式(量化式服务方法调用)= 加 resolver + 注册一行,不改驱动/既有策略。`page→常量` 精确 `uses_api` 边。
- **gate 在 `url_registry` flag**:实测 openclaw 144 个 api_call **0 个 url_registry**(全内联)→ 纯内联项目零影响;ideas-v2 645 个 url_registry 才走精确归因。
- **build_impact_graph 单点过滤**:丢弃 `contains→url_registry常量` 的泛连边(共享注册模块的 contains 被 uses_api 取代),所有 impact 查询统一精确,store 保留 contains 显示不变。
- **验证**:re-ingest 后 702 条 uses_api,`find_api_callers(task/check/container)` **97 页 → 1 页(scanPickProduct.vue)**;`splitPackageTally/splitSubmit` → unpackingTally.vue。

## 四、跨仓 config 版本化:meta.json 进 git + extra_repos 可移植(`5d8a601`)

发现 ideas-v2 / ideas-pda-app 的 `meta.json` 只在 WSL 本地**未跟踪(?? 状态从没 commit)**,PDA 跨仓链接 `extra_repos` 只在用户级 `~/.codev-platform/config.json`(不进 git)→ WSL 重建即丢,无版本化恢复源。

- 两项目 meta.json 补进 git(`repo_url` 为可移植恢复真值源);ideas-v2 加 `extra_repos: ["ideas-pda-app"]`(**project-id 引用**声明跨仓,可移植)。
- `_resolve_repos` 合并两来源:用户 config(机器绝对路径)+ meta.json(project-id 引用→解析成各机 repo_path)。换机/重建 WSL 跨仓关系不丢,绝对路径仍本机 config 自给(换机零改 meta)。非 editable 安装无 platform_meta → 优雅 [] 回退。

## 五、agent 文件工具多仓(`796665b`)—— "多仓抽象的另一半"

agent 经图谱精确查到 PDA 页面后,`read_file`/`list_dir` 却报"路径越出项目仓根"读不到 —— 该页在 extra_repo,文件工具只认单个主仓根。

- **新建 `core/repos.py::project_repo_roots`**:"项目→仓根集合(主仓+extra_repos)"**单一真值源**,graph ingest 与 agent 文件工具共用(原 graph/ingest 的多仓逻辑上提,旧名 re-export 不破调用/测试)。
- `fs.py` 沙箱单根 → 多根:rel 在各登记仓根依次解析命中即读;仍 `is_relative_to` 某仓根才放行(穿越/绝对路径/敏感文件照拒)。`list_dir` 列根目录合并展示所有仓根(各带 header),agent 看得到关联仓结构。fs 多仓 6 测 + ingest monkeypatch 目标随真值源上提更新,509 passed。

## 六、部署根因:web agent = `codev-agent.service`(8848)非 codev-web(18088)

#2~#5 改完,web agent **仍** `found:false` 且 `list_dir("/")` 报**旧错误文案**("越出项目仓根" vs 新码"越出项目所有仓根")—— **铁证:agent 进程跑旧码**。逐一排查发现 web 聊天 agent 跑在**独立服务 `codev-agent`(`cli agent serve`,端口 8848)**,我一直重启的 codev-web(uvicorn web.app:18088)是 admin web-ui 后端。codev-agent **pid 168 启动于昨天 21:53**(我所有改动之前)→ agent 相关修复对它一个没生效。重启 codev-agent 即吃到全部新码。

- **判据沉淀**(记忆 [[web-agent-runs-in-codev-agent-service]]):agent 报的错误文案与新码不符 = 该进程跑旧码,找它**真正的宿主服务**重启。`_run_query` 每次新开只读 store 无缓存 → 数据 re-ingest 即生效,但**代码改动必重启进程**。改 agent 工具/impact/fs → 重启 codev-agent;IDE/MCP 客户端走 codev-mcp-*。

## 七、方法论:逐层实测证伪(本日两次猜错被数据纠正)

1. **"URL 没解析,只存常量名"** —— 我查 `name/id` 没查 `meta_json.url` 误判;实测 PICK_CHECK_TURN 的 meta.url 正确,真因在查询层。
2. **"非代码文件 bloat(word/excel/图片)"** —— 用户直觉;实测 codegraph 不索引二进制,真凶是 17KB 的 Java 方法注释。

两次都是**先实测节点分布/字段长度/边/真跑函数,再定论**。"派对抗审计 + 实测验证"是平台开发的纪律,不是 nice-to-have。

## 八、对抗审计 + 修复(下午段)—— 派 4 兄弟审上午这一大轮,抓修真 bug

上午 commit 链收尾后,派 **4 个对抗审计兄弟**按风险聚类(嵌入管线 / 统一图谱 / 网关鉴权 / agent 多仓隔离)审 6/11晚~6/13 这一大轮。整体评价:对抗痕迹重,安全侧越权/穿越 PoC 几乎全被拦死;抓到并修了:

- **P0(`313a972`)**:`qwen.py` `712588e` 加 batch_size 时**误删 `self._model=None`**,`_ensure` 仍读它 → qwen-local embedder 首次 encode 必 `AttributeError`(零测试覆盖,remote 默认掩盖)。补回 + 回归断言。
- **P1a(`313a972`)**:PG 队列 rc=2(锁占)job 因 lease 隐身最多 1800s,破"下轮重试"契约(file 后端无此问题,PG 迁移引入)。`PgJobQueue.release()` 复位 pending。
- **3×P2(`313a972`)**:`find_api_callers` 路径参数模板匹配(`/users/123`↔`/users/{id}`)/ 契约漂移 0.5 兜底边归 `uncertainCalls` 桶 / 相对 extra_repos fail-closed。+ chroma daemon 接 `bind_host`+`startup_policy_error`(与另三套 MCP 对齐,补反代 passthrough 护栏)。
- **P1b 根治(`9e65067`)**:多仓前端节点 id/file 以仓相对路径为锚无仓维度 → 两仓同 `src/pages/index.vue` 碰撞 → merge first-wins **静默丢后仓节点**。新 `RepoScope` 单一职责单元(写侧 localize 打仓 tag / 读侧 resolve 还原),主仓 tag='' 零 churn。实测 ideas-v2 重建 **783 个 extra 仓节点带 tag 存活**(原会被丢)。
- **/embed 闸(`8b1623a`)**:loopback 豁免在**同机反代**下可被远程白嫖 GPU。复用 `internal_secret` 加 `X-Internal-Call` 信物闸(配 secret 才生效)+ 反代 runbook 排除。token 模式平台**活体验证**:无信物→401、带→200。安全顺序(先重启调用方再重启 daemon)避免 embedding 断。
- **audit 加固(`f912e03`)**:`audit_all_stores` 旧版只 glob sqlite,pg 后端会扫空=门禁形同虚设(潜伏 bug,平台现 sqlite)。改按后端枚举(pg 走 `list_project_ids`)+ store 外 `reconcile_orphan_pids` 孤儿检测。**否决**往 store 注入 allowlist(破 §8+契约对称)。
- **audit 抓出并修的真 bug(`376e108`)**:跑 audit 抓出 codev-platform 图谱 **25 条 dangling** —— A2 给 A3 `inferred_api_call` 软节点发 plays_role,软节点下轮未重产即悬空。Fix-A(plays_role 排除软节点)+ Fix-B(`_analyzers_pass` 的 hard_ids 信任集只含硬节点,防任何 analyzer 跨轮引用软节点)。重建实测 **25→0**。

**质量教训沉淀成规则** → `.claude/rules/code-quality-discipline.md`(本日多次被用户纠"堆代码/深嵌套/死代码兜底/偷懒延后理由")。

## 九、晚间段 —— 代码智能平台蓝图续建(Phase 5/8)+ MCP 可用性根因

用户决定按主计划蓝图把代码智能平台逐个建出来(简单→难)。先核实"审计剩余项"#7 config DI / #8 set_roles 多 org 复核后**都是 stale 已完成**(只订正状态标记);Phase 3 残留核实为**已完成/低 ROI 冗余**(provenance src/parser/pv + 全套 audit + file:line 证据都在;source_line 落每边冗余),跳过不堆码。真有未建实质的从此开始:

- **Phase 5 路径评分(`e7b72a1`)**:find_impact_paths 既有 conf×src 评分上,正交补 `_KIND_WEIGHT`(关系强度,imports/renders/mentions 降权)+ `_DEPTH_DECAY`(近依赖优先)。社区/新鲜度因子留 Phase 4/1 接入,不预埋。
- **Phase 8 观测切片(`9ab5582`/`bcd6bf0`)**:RecallTrace per-query/per-lane 耗时·候选·无证据 best-effort 落 JSONL + `recall_latency_report` 聚合 P50/P95/无证据率/lane 命中率 + CLI `recall-stats`(measure-first)。conftest autouse 隔离 recall_trace 防测试污染真实 baseline。
- **measure → fix → re-measure 闭环(`49e0d01`→`005382c`)**:观测一上线就量出"vector lane 对 codev-platform 0 命中却 P95 944ms"。精准修(非预建 cache):无 code_vec 项目 fs 探活快速跳过。首版用错标记(chroma.sqlite3 空库残留漏判)→ WSL 诊断查真因 → 改用 `.manifest.json`。WSL 实测 **vector P95 944→4.8ms、总 P95 991→24ms**。

**MCP 可用性根因(整场"MCP 不可用"的真相,三层)**:
1. **codegraph 配置冲突(预存)**:codev-platform 是平台仓本身、`.codegraph/config.json` 提交在 git(f9e5cae),无法整目录 junction 到平台数据 → repo `.codegraph` 只有 config 无 db → codegraph MCP "not initialized"。`codegraph link --all` 故意报 conflict 待手动取舍。**修**:symlink 平台 `codegraph.db` 进 repo `.codegraph`(config 留提交版,db 走 `*.db` gitignore)+ 重启 codev-mcp-codegraph。本地 symlink 不进 git(同其它项目 junction)。
2. **会话 SSE 连接陈旧(自造)**:晚间部署反复 `systemctl restart codev-mcp-graph/codegraph` 把长跑 Claude Code 会话连接打断 → MCP 调用全 `-32602`(server 健康、client 连接死)。**修**:重启 Claude Code 重连(非 /clear)。重启后实测 codegraph_search / graph search_nodes 全通。
3. **过程失误**:整场把"MCP 不可用"当借口没真试、理由(worktree 特定)错。教训记 [[wsl-mcp-daemons-stale-after-pull]]:会话中途别反复重启 MCP 服务;先测一次再断言。

## commit 链(2026-06-13 段)
**上午(PDA 链路)**:`712588e`(code_vec batch+skip_kinds 配置)→`a0485ef`(/embed 反应式 OOM 回收)→`6530886`(续跑探活 R5)→`ba05cf9`(find_api_callers URL 解析)→`6cfa250`(前端 API 使用精确归因 uses_api 引擎)→`5d8a601`(meta.json 进 git + extra_repos 可移植)→`f3704ac`(build_text 封顶治超大 docstring)→`796665b`(agent 文件工具多仓 + core/repos 单一真值源)。
**下午(对抗审计修复)**:`313a972`(审计批: P0 qwen/P1a reindex/3×P2/chroma bind)→`9e65067`(RepoScope 多仓节点碰撞根治)→`8b1623a`(/embed 内部信物闸)→`f912e03`(audit 按后端枚举+孤儿 reconcile)→`376e108`(A2/A3 悬空 plays_role 根治 25→0)。
**晚间(蓝图续建)**:状态订正 `313a972`后`f413fac`(#7)/`3c56b3c`(#8)/`70a8daa`(conftest 注)→`e7b72a1`(Phase 5 路径 kind 权重+深度衰减)→`9ab5582`(Phase 8 观测 trace+聚合)→`bcd6bf0`(recall-stats CLI + conftest 隔离)→`49e0d01`/`005382c`(vector lane fs 探活快速跳过, P95 944→4.8ms)。MCP codegraph symlink 修复为本机 local 不进 git。

## web 端
本会话改动**不需前端同步**:impact.py / api_usage / core.repos / fs.py / code_vector_store 全在 graph 引擎 + agent 工具 + 索引层,OpenAPI 未变,`pnpm run api` 不用跑。

## 运维落点(本日做的服务重启)
- 索引代码改动 → 续跑 build 是直接 python 进程(无 worker 超时),按需重启 `codev-mcp-platform-docs`(/embed)。
- impact/uses_api 生效:re-ingest ideas-v2(`reindex-queue enqueue --kind ingest`,worker 跑)+ 重启 `codev-mcp-graph`(IDE/MCP)+ **`codev-agent`(web agent,本日关键)**。
- 多仓 fs / find_api_callers 给 web agent 生效:**重启 `codev-agent`**(非 codev-web)。

## 结论
06-12 §十那条"服务大型复杂多仓项目"的工程化硬骨头,本日**收尾闭环**:大项目 code_vec 建成、跨层链路查询(端点→精确页面)可用、关联仓代码可读、跨仓 config 版本化。剩 codegraph/code_vec 索引 extra_repos(`code_recall` 多仓)是更大单独工程(codegraph sync 按设计只扫主仓),但"查端点调用方 + 读页面代码"用例已由 graph find_api_callers + 多仓 fs 完整覆盖。
