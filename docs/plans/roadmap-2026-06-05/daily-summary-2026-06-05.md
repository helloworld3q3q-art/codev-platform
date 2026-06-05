# Daily Summary — 2026-06-05(W1 统一图谱 MCP + cross-link 退役 / W2 Track M memory 全栈 + B1 / W3 loop-guard 重构)

> 范围:三窗口并行(W1 图谱 / W2 memory / W3 loop-guard)。本文 **W3 段**(§一~§五 + 附:chroma 事故)+ **W2 段**(§六~§十三)+ **W1 段**(§十四~§十六)。
> 关联:`agent-loop-guard-redesign-2026-06-05.md`、`dev-agent-memory-mcp-design-2026-06-05.md`、`next-plan-2026-06-05.md`。
> commit:W3 = `e3c6594`+`9246664`;W2 见 §十一 清单。均已 push 到 `fuwuqi/dev`。

---

## 一、交付:loop guard 从「全局按工具计次」升级为「按工具语义三分类施策」

`per_tool_cap=3` 把「读 3 个不同文件」误判成空转拦死(aa.txt 实测 agent 明说「因工具调用限制未读取到」),而真正该防的「弱模型换词 thrash」单一 cap 又防不住。重构按工具语义分三类:

- **只读类**(`read_file`/`list_dir`):归一化 distinct-path 上限(高,默认 20)+ 总读软顶(默认 30),同 path 忽略 offset/limit 判零增量。读不同文件 = 确定进展,近乎不限。
- **检索类**(`codegraph_*`/`search_docs`/`impact_*`):distinct-args 上限(`retrieval_distinct_cap`)+ **输出侧零增量**(归一化结果哈希,连续 `no_progress_limit` 次无新增 → 软提示→硬拒→强制收尾)。
- **无效调用类**(参数报错/路径不存在/非法 module):连续 `invalid_call_limit` 次 → 回灌合法值清单 + 强制换路,不放行无限重试。
- **收尾合规**:near_limit 提示加「禁脑补未读内容」;读取充分性门——distinct 成功读 < 阈值 **且尾部连续无效** → 判「卡无效调用」,区分「空转」与「读够了」。

**模型无关铁律守住**:`loop.py` 零模型名 if-else;模型差异 100% 落 `LoopPolicy` 数值/开关,经 `registry.loop_policy()._pick` 逐字段解析(`agent.providers.<name>.loop.<f>` > `agent.loop.<f>` > spec 档默认 > 全局默认)。能力档矩阵 `_STRONG`(claude/gpt,关 novelty + 宽 cap)/`_MID`(qwen)/`_WEAK`(deepseek),加模型 = 选档一行,核心零改。`per_tool_cap` 保 deprecated 别名映射 `retrieval_distinct_cap`(不破存量 config/测试)。

## 二、P0:放开 `search_docs` 的 module 枚举(chroma/_schema.py)

aa.txt 实测 codev-platform 的 `module="web-ui"` 被 MCP `inputSchema` enum 硬拒(enum 写死 stock-* + platform)。选**方案②**:去掉 `module` 硬 enum → 自由字符串 + 描述引导用 `list_collections` 看本项目模块;`_build_where` 对未知 module 优雅返空不报错。理由:逐项目动态模块集用 schema 静态 enum 是错误机制,放开比加项更彻底(对所有项目一劳永逸)。`category` enum 保留(它是固定集)。

## 三、审计 + 测试(两轮)

- **第一轮**:审计兄弟(无 BLOCKER/MAJOR)+ 测试兄弟(Gate 覆盖核查),据此修 1 个真 MINOR(充分性门加 `consecutive_invalid > 0`,纯检索任务不再被误判卡无效)+ 补 2 个回归测试(P0 schema 无 enum、P2 归一化抗加空格/标点)→ `9246664`。
- **第二轮(完整复审最终态)**:**38 passed / 0 failed**,无 BLOCKER/MAJOR;对抗项(零 if-else / frozen 别名 / `_bool` 三路 / novelty 边界 / 充分性门双向 / 去 enum 无遗漏)全 PASS。残留仅 MINOR(`_bool` 未带 `legacy_keys`,当前无 bool 字段需要别名,不补——避免为未来写代码)。结论:ship as-is。

## 四、端到端验证(WSL 平台)

- WSL 平台仓 `/home/helloworld/work/codev-platform` 已在 `9246664`,P0 代码在位。
- `agent_tool_health.py`:**11/11 工具**端到端健康(codegraph / cross-link / search_docs / read_file / list_dir / remember)。
- **P0 live 确认**:重启 chroma daemon **前**,daemon 仍拒 `module="web-ui"`(`Input validation error: 'web-ui' is not one of [...]`)——**反向坐实** MCP `inputSchema` enum 是**服务端强校验**;重启**后** `module="web-ui"` 与未知 module 均 `rejected=False` 返空,基线 module 正常有结果。
- daemon 重启干净:旧 PID → 新 PID,`serve-mcp start --wait` 三端点 OK,cross-link/codegraph `already-up` 未受影响。

## 五、踩坑 / 教训

1. **多窗口并行禁 `git add -A`**:首个 commit 被 `-A` 卷入 W2 未提交的 10 个文件。未 push 时 `git reset --soft HEAD~1` + `git restore --staged .` 拆出来,W2 改动原样留回工作树(零丢失,后由 W2 窗口自己提交成 `f656c6a`)。沉淀为 memory `multi-window-git-add-scope`。落点:`workflow.md §6.1`。
2. **改 MCP schema 必重启 daemon**:`inputSchema` enum 服务端强校验,daemon 不随 pull/commit 热重载(MEMORY `wsl-mcp-daemons-stale-after-pull`)。`serve-mcp` 无 `restart`,reload = `pkill -f chroma.server` + `serve-mcp start --wait`。
3. **WSL bridge 引号坑**:`wsl.exe bash -c '<payload>'` 会把 payload 再包一层双引号,内含 `"`/`|`/`()` 会被外层 shell 重解析 → 复杂命令写成 `.sh` 文件用 `wsl.exe bash <file>` 跑,免引号穿层。

## 附(W3)、chromadb 1.5.9 多 flush compaction 事故 + 修复(dogfood 衍生)

> 完整复盘:`docs/incidents/2026-06-05-chromadb-multiflush-compaction.md`。修复 commit `87de379`。

**起因(误诊连锁)**:dogfood 跑里 openclaw-stock 的 `search_docs` 返回 codev 文档,被我误判成 bug。逐层误诊(路由失效→污染→磁盘→并发→settle),每层都被下一步证伪;期间 `--force` 猛刷共享 chroma 库,把 `chroma.sqlite3` 彻底搞坏(`database disk image is malformed`),search_docs 一度全项目下线。**真相**:那"污染"是**设计内**——`platform/.claude/index.json` 的 `external_doc_paths` 故意引 codev 文档作跨仓真值源(只要先读一眼 index.json 就免了整起事故)。

**真根因**:chromadb 1.5.9 的 compaction —— 对一个 collection 做**多次** flush(`col.upsert`)、而库里已有别的 collection 时,会写坏 sqlite(`Error purging logs` / `Failed to pull logs` / `disk I/O 522`)。**单次 upsert 不犯**;构建顺序/并发/路径/settle 都不是变量(全证伪)。平台之前没事是因为原 3 collection 是**增量**小 upsert 建起来的,只有全量 `--force` bulk 重建才触发。

**修复**:`indexer.py` 的 flush 阈值 `BATCH` 默认 100 → `int(os.getenv("PLATFORM_INDEX_FLUSH_BATCH","50000"))`(现实库=单 upsert,彻底绕开;env 兜底超大库)。恢复流程:停 daemon + `reindex-queue worker` 独占 → `rm -rf data/chroma` → 逐项目单 upsert 重建 → **干净重起 daemon**(陈旧 daemon 报错会误导)→ 实测 search 恢复(codev 1632 / openclaw 5154,各返自己文档)。沉淀 memory `chromadb-multiflush-compaction`。

**教训**:① 动共享数据(尤其破坏性 `--force`)前先确认"真是 bug 吗",能只读验证就别动手;② 全量重建 chroma 必单 upsert + 独占(chromadb 持久库非多进程写安全);③ 重建/改 schema 后必"干净重起 daemon"再判断。**chromadb 降稳定版**留作下次依赖升级评估(单 upsert 默认已让当下不依赖它)。

---

# W2 段 —— Track M(开发端 memory 接平台)全栈交付 + B1 向量召回

> 范围:`dev-agent-memory-mcp-design-2026-06-05.md` 的 Track M(P0→P3)+ 后续 B1。四轮(每阶段一轮)独立审计全 PASS。

## 六、Track M P0 —— 写侧鉴权三缺口前置修复

memory 写侧此前三个真缺口(安全+质量专家独立抓到):① `remember` 工具不写 `topic_key`/`is_redline` → 永不去重;② `RememberTool.run` 绕过 `audit_access`+`_scope_decision` → 无授权无留痕;③ `is_redline` 由 client 直传无写闸 → 可冒造 org 硬约束。修复:
- 抽 `agent/memory_authz.py` 单一真值源:`make_topic_key`(三写入端统一 slug,Unicode `isalnum` 保留中/日/韩/音标)、`scope_decision`(route+工具同一道闸)、`redline_write_allowed`(仅 org admin)。
- 真 identity 经 `RunContext` 透到工具(token 模式 project 写不被误拒);IDE 写路径恒不写 redline。
- commit `f656c6a`(主)+ `b6dd256`(审计 NIT:slug 改 Unicode isalnum,避免韩文等丢 key)。

## 七、Track M P1 —— memory MCP 前门读侧 MVP

新建 `agent/memory_mcp.py` SSE 前门(对称 cross-link),只读暴露 `recall`+`list_scope`;**org/user 从认证身份绑 contextvar,绝不由 client 传**(身份红线);`mcp_serve` 注册第 4 端点(18087/19087)+ systemd unit + `.mcp.json`。commit `19942c3`。审计 PASS(contextvar 生命周期对照 MCP SDK 核实不串租户)。

## 八、Track M P2 —— 写侧 + 迁移

- `memory_mcp` 加 `remember`(默认 personal)/`forget`/`supersede`:scope_decision+audit 同闸、redline 恒不写、owner+org SQL 隔离;store `forget`/`supersede` 加 owner-scoped + `protect_redline` 闸(IDE 不得删改 redline)。
- `codev-platform memory import-md <path>` CLI:逻辑进包 `agent/memory_import.py`(wheel 通用),`scripts/migrate_memory_md.py` 退薄 shim;默认 dry-run、topic_key 归一幂等。
- commit `2bc7362`(写工具)+`9c8e8ae`(import-md)+`346d3b7`(审计 NIT:`protect_redline`)。

## 九、Track M P3 —— 多 dev 护栏 + 可见性 CLI

- `gateway.multi_user_policy_error`:`multi_user=true` 或 >1 登记 token 而仍 passthrough → memory MCP / agent serve **拒绝启动**(fail-fast,堵"单人 WSL 变多人共用却没切 token"的过渡空窗,不靠人记得)。
- `codev-platform memory list --scope`:可见性 CLI,personal 只看本机自己的(store 物理隔离保证 personal 对 org 不可见)。
- commit `2e0d6b9`+`facb208`(config.example)+`78e7dce`(审计 NIT:去重 load_config)。

## 十、部署 + e2e + B1 向量召回

- **WSL 部署上线**:agent-memory 端点 systemd `enabled`+`active`(reboot 自起)on 19087,`memory_enabled`+`rbac_enabled`;只装 agent-memory 单 unit,不动正在跑的另 3 个。WSL config 补 `mcp.agent_memory_sse_port: 19087`。
- **换机同步 e2e 实测 PASS**(`scripts/e2e_agent_memory_sync.py`,驱动真实 19087):A机 remember → B机同 user recall **命中**(记忆跟人走)、C机他 user recall **未命中**(隐私隔离)、forget owner 限定生效。commit `5b5c74e`。
- **B1 向量语义召回**:step1 `VectorRecallService`(RRF 关键词∪向量,redline 永置顶,前置 scope/冲突消解全保留)+ `MemoryVectorIndex` 接缝(`5cef5b0`);step2 真链路 per-org chroma `<org>__agent_memory` + Qwen-CPU 嵌入 + 写时 embed 装饰器 `VectorSyncMemoryStore` + deps `recall_backend=vector` 接线(`a124179`)。**WSL 实测 PASS**:语义>关键词("深色模式配色"命中"暗色主题")、向量查询带 ACL where 过滤、redline 仍置顶。审计亮点:`vec_ids ∩ active 池`是真 ACL+新鲜度硬闸,陈旧向量索引无法泄漏。`84aa488`(docs)+`23b86db`(审计 NIT)。

## 十一、踩坑 / 教训 + commit 清单(W2)

踩坑:
1. **多窗口 `git add -A` 反向被坑**:W2 的 10 个文件曾被 W3 窗口 `-A` 卷走;此后 W2 全程**显式 `git add <文件清单>`**,与并行 W1(A1 图谱)/W3 提交干净分离。
2. **PowerShell→wsl→bash 三层引号**:内联 python `-c` 多次被外层重解析炸 → 复杂 python/JSON 改写**临时 .py 文件**(Windows temp)再 `wsl python /mnt/c/.../x.py` 跑,免引号穿层。
3. **WSL 仓经 Gitea 自动拉**:`~/work/codev-platform` origin=Gitea(localhost:3000),push `fuwuqi dev` 后 WSL 自动 pull;editable venv 改 .py 即生效,但 systemd daemon 不热重载(改码需 restart)。
4. **`pkill -f <模式>` 自杀**:`pkill -f codev_platform.agent.memory_mcp` 把承载该命令的 bash 自身一起杀了(命令行含同模式)→ 杀进程用更窄模式或 pid。

commit(均 push 到 `fuwuqi/dev`):`f656c6a` `b6dd256`(P0)/ `19942c3` `6d70f06`(P1)/ `2bc7362` `9c8e8ae` `346d3b7` `97af3ae`(P2)/ `2e0d6b9` `facb208` `78e7dce`(P3)/ `5b5c74e`(e2e)/ `5cef5b0` `a124179` `84aa488` `23b86db`(B1)。Track M plan 序 `M→B1` 已走完。

## 十二、B2 —— M4 维护 cron + 向量 GC(闭合 B1 审计 NIT)

- **向量 GC**:`MemoryMaintenance` 加 `vector_index`;`compress_topic` 归档原条时同步删其向量(有 id+org_id 精确删),闭合 B1 审计 NIT —— 此前 archive 不经 store decorator 同步向量 → 索引堆积陈旧向量(虽被 `vec_ids ∩ active 池`闸防泄漏,但冗余膨胀)。GC 失败 swallow 不拖垮维护主流程;`deps.get_memory_maintenance` 传 `get_memory_vector_index()`。
- **M4 cron**:`mcp_systemd.render_memory_maintenance_units` —— `codev-memory-maintenance` oneshot service + 每日 04:00 timer(跑 `run_memory_maintenance.py` 无 args = TTL 归档 + 向量 GC;**LLM 压缩仍手动**)+ 并入 `install_systemd`;单实例锁与手动跑互斥。
- **WSL 实操**:授权切 `recall_backend=vector` + 重启 agent-memory,**vector 模式 e2e 复跑 PASS**;maintenance live 跑通(空库 TTL 0、compress 跳过、无崩)。config 备份 `config.json.bak.vecbackend`。
- 测试:`test_agent_memory_maintenance.py`(GC 删原条/无索引 no-op/GC 失败不崩)+ `test_mcp_serve.py`(timer unit 渲染)。commit `4f53705`。

## 十三、C2 —— memory 压测摸底(收尾)

`scripts/bench_memory.py`:隔离 `bench-mem-org` seed N 条 + 测 write/recall P50/P95(local 与 vector 各一遍),跑完自动 cleanup(DELETE bench org 行 + 删 bench chroma collection),不污真数据。纯 `percentiles` 单测(`tests/test_bench_memory.py` 4 passed)。WSL 实测(真 PG + Qwen-CPU):

| backend | write P50/P95/P99 | recall P50/P95 | 吞吐 |
|---|---|---|---|
| **local**(n=500) | 3.75 / 5.15 / 7.25 ms | 1.13 / 2.53 ms | ~265 写/s |
| **vector**(n=30, CPU embed) | 3588 / 7570 / 12093 ms | 1284 / 1810 ms | ~0.2 写/s |

**结论**:
- **local 亚 10ms**,且 recall 受 `per_scope_limit=50` 上限保护 → 与总量 N 解耦,N 万条也不退化。当前/中期规模 local 完全够。
- **vector 被 Qwen CPU 嵌入完全主导**(写 ~3.5s、召回 ~1.3s,慢 local ~1000×);chroma ANN 不是瓶颈(嵌入是)。写低频可接受;召回 ~1.3s 交互可忍但偏高。
- **取舍**:要 vector 的语义质量又想压延迟 → `memory.embed_device=cuda`(GPU 嵌入快 ~10-50×),代价是与 chroma daemon 抢 8GB GPU(OOM 风险,需 `gpu_concurrency`/显存盘点)。默认 `cpu` 是"安全但慢";延迟敏感场景仍建议 local。
- **数据未固化**(--keep 可留作 B1 benchmark);本次摸底用完即清。commit:见 §十一 末追加。

---

# W1 段 —— 统一图谱 MCP(A1 消费前门)+ cross-link 退役

> 范围:W1 图谱线。给 A1 综合理解层补上开发端消费前门(graph MCP),并完成 cross-link 彻底退役收口。commit 见 §十六,均已 push 到 `fuwuqi/dev`。

## 十四、统一图谱 MCP —— A1 业务域 + impact 接到开发端 agent

A1 标注准(95%)但"标签躺图谱里没人用"——缺开发端消费前门。新建 `graph/mcp_server.py`(第 5 套平台 MCP,多租户 SSE 镜像 cross-link),给开发端 Claude Code/Codex 暴露 8 工具:跨层影响 5(`find_impact`/`find_table_usage`/`find_page_dependencies`/`find_impacted_pages`/`find_api_callers`)+ A1 业务域 2(`find_node_domain`/`list_domain_members`)+ `search_nodes`(模糊搜,退役 cross-link 前补)。`dispatch` 抽纯函数可测(绕 MCP 装饰器)。接 `serve-mcp` 第 5 端点(local 18092 / platform 19092)+ config + systemd unit。commit `807b65a`/`eeea201`/`3820651`/`61dfa16`(search_nodes)。

## 十五、cross-link 彻底退役(6 批,-3300 行)

核实发现 **cross-link 的 MCP 工具早就只读统一图谱 store**(老 `cross_layer.sqlite` 数据角色 2026-06-03 退场),graph MCP 已承接其能力(`find_table_usage`≈`find_table_refs` / `find_api_callers`≈`find_endpoint_link` / `search_nodes`)。**4 专家会诊**核实"放弃不丢能力",否掉一个关键误判:统一图谱 `builtin.sql` 插件**其实已扫 Java 注解 SQL + MyBatis-Plus**(`_scan_java_dml`/`_scan_mybatis_plus`)→ Java 表血缘已覆盖;stock 用注解 SQL 非 XML Mapper(全仓 0 个 `*Mapper.xml`),无缺口。据此分 6 批退"外壳":

- **批1 编排**:mcp_serve 停 cross-link 端点 + config + .mcp.json(`8ca21e5`)。
- **批2a metrics**:删 cross-link 源(删包护栏 —— 不再 import 该包)(`1ac12f0`)。
- **批A ops 观测**:health(`_check_cross_layer`/`_check_cross_link_mcp`/`_usage_cross_link`)+ logs/gateway/bootstrap/backup(`af9dfea`)。
- **批C web/dashboard**:platform_status + `web/schemas/reports` + 前端 `McpUsageCard.tsx` 删 cross-link 列(`e709338`)。
- **批D 删包+reindex**:删整包 + Java/TS 扫描器 + reindex scope/runner/A3 `_AUTO_REINDEX_RETIRED` 机制 + systemd(顺手补 graph unit,修接线漏),**-3063 行**(`c57b309`)。
- **批B 文档**:ai-tools-mcp/workflow/README 的 cross-link → graph + `sync-rules`(`5be567e`)。

**验证**:全套 **1050 passed**(5 fail 是本机缺 `jieba` 的 W2 vector 测试,与退役无关)。**运维待办**(代码已就绪):① WSL `serve-mcp` 重启停还在跑的 cross-link daemon(否则旧进程跑已删代码)+ 起 graph 端点;② 前端 `pnpm run api` 重生 typings(删 `crossLink`);③ 业务仓 `.mcp.json` 删 cross-link 块。沉淀 memory `cross-link-retired-graph-takeover`。

## 十五续、退役完整收口 + 深度审计(BLOCKER+死代码)+ XML Mapper 补盲

§十五 的 6 批退了"外壳",但**全仓残留 + 深度审计**揭出更多,多轮收口至活引用清零:

- **全仓残留清理(多批)**:skills(update-local-ai/ai-health/git-commit 的 reindex 档/usage/scope)/ docs(ai-toolchain-guide/USAGE/onboarding 的 .md **+ 此前漏的 .html**)/ cli(mcp-source/builtin 提示)/ ops.logs / 16 文件描述列举注释 / ai-tools-mcp `-SkipCrossLink` → graph。`sync-rules`/`sync-skills` 重生 `.claude/` 副本。
- **深度审计(4 对抗 agent)**:① **BLOCKER** `webhook/server.py` 调已删的 `auto_reindex_kinds`(批D 删后某 commit 又引用却没恢复函数)→ 每个 webhook push 运行时 ImportError 崩(函数内 import 无测试覆盖),改 `list(classify_scopes(...))`(`e58a7f7`);② **死代码** `tools/cross_link/`(import 已删包)+ `tools/audit_graph_parity` + `eval run_crosslink` suite + `core/paths` cross_link_db_path/legacy(全仓 0 活用户)+ 死字段 `meta["cross_link_rel"]`(只写不读)+ web REST 组已删的 dangling 注释,**-976 行**(`6a849a8`);③ scripts/*.ps1(`update-local-ai.ps1` 跑已删的 `cross_link.build_index` 会 ModuleNotFoundError)+ post-commit/dirty-index-check/ai-health,**-236 行**(`fb02e19`)。功能链路(serve-mcp/health/metrics/reindex/dashboard/graph MCP)审计全 CLEAN,graph 8 工具承接无真损失。
- **XML Mapper 补盲(审计揪出的能力缺口)**:sql 插件只扫注解 SQL(Pass 4b)+MyBatis-Plus(4c),**XML Mapper(`*.xml <select>`)静默不扫** → 用 XML 的 Java 仓"表↔Java"血缘整段丢且 detect 不报。补 **Pass 4d**(`sql/xml_mapper.py`,按 `agent-provider-architecture` 铁律做成 sql 插件内**扫描域非新顶级插件**:三种 MyBatis 写法=同职责三 Pass):`<select>` SQL 去动态标签(`<if>/<where>/<foreach>`)+CDATA 解包+占位符,复用 `_sql_table_access`/`_emit_table_access` 产 `backend_function(java)`+reads/writes_table 边。端到端 demo(动态标签/JOIN/CRUD/CDATA/namespace 全验)+6 单测 PASS(`4992171`)。
- **反复"还有么"逐行普查**(用户连追,每轮挖出越来越细的残留):`eval/datasets/retrieval.jsonl` 2 评测 query 用 cross-link 关键词召回不到 → graph / `web-ui/README.md` API 路由 `/graph/cross-link/graph`(端点已删)→ `/unified/graph` / `config.example.json` projects+data 段注释 / `.gitignore` cross_link_asm/ts artifact 注释 / `test_web_reports` mock fixture `crossLink` 字段 / `wait-for-reindex.ps1` `$crossLinkPattern` **活逻辑**对齐 post-commit 两类 scope / paths+ops.codegraph+ai-tools-mcp "与 cross_layer.sqlite 并排" 失效对照 / ai-toolchain-guide.html 锚点 id。**逐行完整核查(Grep 工具,非抽样)确认:活引用(代码/配置/脚本逻辑/前端/测试/eval/API 文档)100% 清零**;旧工具名 `find_table_refs`/`find_endpoint_link`/`cross_link_stats` 全零命中(早改 `find_table_usage`/`find_api_callers`);剩余全是历史注释/反向断言守护(`assert cross_link not in`)/auto-gen typings/归档留痕/untracked 临时文件。

**验证**:各批测试全绿(plugin+ingest 153 / 全套 1041~1050 passed,5 fail 恒为本机缺 `jieba` 的 W2 vector,与退役无关)。

## 十六、commit 清单(W1)

均 push 到 `fuwuqi/dev`:`807b65a` `eeea201` `3820651`(统一图谱 MCP)/ `61dfa16`(search_nodes)/ `8ca21e5`(批1 编排)/ `1ac12f0`(批2a metrics)/ `af9dfea`(批A ops)/ `e709338`(批C web)/ `c57b309`(批D 删包)/ `5be567e`(批B 文档)。

**退役完整收口(同日续,均 push `fuwuqi/dev`)**:`2afd3be`(残留清理 -976)/ `fcd5b83`(补漏)/ `e58a7f7`(webhook BLOCKER)/ `6a849a8`(死代码 -976)/ `fb02e19`(scripts -236)/ `4992171`(XML Pass 4d +183)/ `3fb2adf`(.html/前端/memory)/ `a6a4bdf`(wait-for-reindex)/ `a037bf6`+`adb2676`(eval/web-ui/config 末梢)/ `e0a785d`(周边失效对照)。

---

# W2 续 —— 召回可插拔重构(解构 P0→P3)

> 范围:把召回从 monolith(`recall_service.py` 里 Local/Vector 两个具体类)重构成**可插拔流水线**。
> 用户诉求原话:"解构,可插拔,可以配置,不要堆代码" + "两个 Qwen 未来要换更好的模型" + "不用那两个模型和 bm25 也能跑虽然效果不好"。plan:`memory-recall-pluggable-pipeline-2026-06-05.md`。commit 见 §二十一,均 push `fuwuqi/dev`。

## 十七、架构 —— 固定不变量 + 可插拔三段 + 双 registry(套用 agent-provider 哲学)

照搬 `agent-provider-architecture.md` 的"协议族 adapter + registry + config 驱动 + 零 if-else":
- **固定不变量**(在 `recall/service.py:PipelineRecallService` 核心,不可插拔):ACL `visible_scopes` 过滤 → `resolve_conflicts` 去重 → **redline 永置顶** → top-N 截断。这四条是安全/正确性红线,任何 scorer/reranker 都改不动。
- **可插拔三段**:`Scorer`(候选打分排序)→ `Fusion`(多 scorer 结果融合)→ `Reranker`(精排),全实现 `recall/base.py` 抽象。
- **双 registry 零 if-else**:`recall/registry.py` 按 config 装 scorers/fusion/reranker;`embed/registry.py` 按 config 装 Embedder/RerankModel。加一档 = `register_*` 一行 + 一个 adapter 类,不动 service/build。
- **降级地板(原则 #5)**:任何档依赖缺(向量库 None / jieba 缺 / 模型未装 / daemon 不可达)→ 工厂返 None → 跳过该档;scorers 剔空 → 强制补 `KeywordScorer` 地板。**保证零依赖也能跑出可用 pipeline**(即用户要的"不用模型和 bm25 也能跑")。

`recall_service.py` 降为 back-compat shim,`LocalRecallService`/`VectorRecallService` 变 `PipelineRecallService` 薄子类 —— 行为等价由旧测试守(P0 重构后旧测试 60 passed,零回归)。

## 十八、P0 解构 + P1 BM25

- **P0**(`9c96afe` + `d155982` 测试):拆 `recall/` 包(base/scorers/fusion/reranker/service/registry)。`RrfFusion` **自包含 RRF**(公式 `Σ 1/(k+rank+1)`,稳定排序,不 import `chroma`/`core.ranking`)—— 刻意与 W1 正在做的 rrf 迁移解耦,免被其 churn 带崩。审计/测试兄弟过 P0。
- **P1**(`060143a` + `e0c65ce` NIT 补测):`Bm25Scorer`(jieba 分词 + rank_bm25,纯 CPU,复用 `chroma.bm25.tokenize`),缺 `rank_bm25`/`jieba` 自动降级跳过。审计 NIT:补 tie-break 全 0 分保候选池原序、单文档语料不崩、chromadb 缺失短路降级。

## 十九、P2 模型可换 + 共享实例(避第二份 GPU 模型)

- **P2a 嵌入 registry**(`bb65cb8`):`embed/` 包(qwen/registry)。`QwenLocalEmbedder` 从 `memory_vector_chroma` 提出;`build_embedder(cfg)` 按 `memory.embed.backend` 选(默认 `qwen-local`),device 优先级 `embed.device > embed_device(旧别名) > cpu`。**换模型零核心改**(用户诉求兑现)。
- **P2b 共享嵌入**(`8510bc1`):关键决策 —— 不让 agent-memory 再 load 第二份 Qwen(8GB GPU 会 OOM,见 memory 教训)。chroma daemon 加 `POST /embed`(复用已加载模型 plain encode),`RemoteEmbedder` RPC 调它。**WSL live PASS**:RemoteEmbedder dim 1024,e2e 走 remote 近瞬时(对比 CPU 本地 ~3.5s)。

## 二十、P3 共享重排(QwenReranker)

- `5d1b3b0`:chroma daemon 加 `POST /rerank`(复用 `_rerank_scores` + GPU 信号量);`RemoteRerankModel` + `recall/reranker.py:QwenReranker`(精排前 top_k,tail 原样接后,打分失败/数量不匹配不动序);`memory_vector.py` 加 `RerankModel` 抽象;registry `register_reranker("qwen")`。**默认关**(`rerank=none`;量小边际收益低,量大一行开)。
- **WSL live PASS**:`/rerank` 相关文档 **0.9961** vs 无关 **0.0001**;`QwenReranker` 把 `[ui, db]` 重排成 `[db, ui]`,全程走 chroma daemon 那份共享 GPU reranker。

## 二十一、审计 + 能力矩阵 + commit 清单

- **审计**:P0/P1/P2a 已审(各自落项);**P2b+P3 审计 PASS-with-nits 无 BLOCKER** —— 安全(`/embed`/`/rerank` 在 AuthMiddleware 后不裸暴露 + 输入校验 + GPU 信号量真串行)、不变量(redline 在 scorer/reranker 之前拆出,reranker 拿不到)、降级、行为一致(plain encode 与 doc 侧一致)全 PASS。3 NIT **已全清**:① chroma handler 校验提到模块级 `validate_*` 纯函数 + 12 单测(`a076f73`,二次审计 PASS 行为 100% 等价);② 补 RemoteRerankModel 真实远端失败 → QwenReranker 不动序 e2e 测(`a1612a1`);③ `memory.embed.timeout` config 驱动(`a1612a1`)。
- **能力矩阵(用户诉求逐条兑现)**:解构 ✅ / 可插拔 ✅ / 可配置 ✅ / 不堆代码(加档=1 行+1 类)✅ / 模型可换 ✅ / 共用一个模型实例(remote RPC 复用 chroma daemon)✅ / 无模型无 bm25 也能跑(keyword 地板 + 全档降级)✅ / BM25+reranker 可配置开关 ✅。
- **MCP 端口/配置键统一**:设计(`1ffc4fd`,`mcp-port-config-unification-2026-06-05.md`)+ 实现 P0→P2 **全落地**(见 §二十二)。
- **commit**(push `fuwuqi/dev`):`9c96afe`(P0 解构)/ `d155982`(P0 测试)/ `060143a`(P1 BM25)/ `bb65cb8`(P2a embed registry)/ `e0c65ce`(P1/P2a NIT)/ `8510bc1`(P2b 共享嵌入)/ `5d1b3b0`(P3 共享重排)。

## 二十二、MCP 端口/配置键统一(P0→P2 实现)

接 §二十一,把散落的 MCP 端口配置收敛(设计 `mcp-port-config-unification-2026-06-05.md`),全程 back-compat:

- **P0 端口解析收敛**(`1b9c3b3`):`mcp_serve.py` 加 `_SERVICE_PORTS` 服务键注册表 + `_bind_port(cfg, kind)` 单一端口真值入口 + `_warn_deprecated`(一次性 warn)。4 套统一 canonical 键 `mcp.<service>_sse_port`;chroma 历史键 `daemon.port` 降为 **deprecated 别名**(仍可读,命中 warn 一次,canonical 优先)。`iter_endpoints` 4 处端口改走 `_bind_port`;`mcp_systemd` 用 `ep.port` 自动跟随,无需改。
- **P1 local 派生 + 一致性 WARN**(`4aaaebc`):`mcp_source_endpoint` 的 **local 端口缺省派生自 `_bind_port`**(本机连本机,改 bind 口自动跟随,不用两处手对齐);`DEFAULT_MCP_SOURCES.local` 去写死端口只留 host;显式 `mcp_sources.local.<tool>` 仍覆盖;platform(远程)端口不变。加 `check_port_consistency(cfg)`:显式 local 端口 ≠ bind 口 → WARN(不阻断,反代场景合法),`serve-mcp status` 末尾打印。
- **P2 样本 + 文档**:`config.example.json` `mcp` 段补全 4 canonical 键(含此前缺的 `platform_docs_sse_port` / `agent_memory_sse_port`)+ `daemon._comment` 标 deprecated 别名 + `mcp_sources.local` 去写死端口改注释说明派生;真值源 `resources/rules/ai-tools-mcp.md` §一b 端口说明统一 4 canonical 键 + 派生/WARN,`sync-rules` 重生 `.claude/` 副本。
- **测试**:`test_mcp_serve.py` 28 passed(P0 新 5:canonical 胜/默认/daemon.port 别名一次性 warn/canonical>别名/iter_endpoints 用 canonical;P1 新 8:派生默认/跟随 canonical/跟随 daemon 别名/显式覆盖/platform 不变/WARN 命中/派生无 WARN/显式相等无 WARN)。
- **不做**(设计 §八):不重命名 `daemon.port` 物理键、不改 env `PLATFORM_DOCS_DAEMON_PORT`(部署接口)、不做端口自动分配、一致性只 WARN 不阻断。
- **审计 + canonical gap 收口**:派审计兄弟过 P0→P2 + gap 修(`1b9c3b3`/`4aaaebc`/`c488217`/`a1612a1`)—— **PASS-with-nits 无 BLOCKER**,back-compat 经验证不破(只配 `daemon.port=19083` 时 5 处全对到 19083)。审计 grep 全仓揪出**另外 3 处裸读 `daemon.port` 漏网**(`agent/tools/search_docs.py` / `ops/gateway.py` / `ops/health/_checks.py`):只读别名故现网不破,但只配 canonical 键时会错回落 18083 —— 与已修的 embed registry 同类 gap,一并改走 `_bind_port`(`677320a`),至此**全仓再无裸读 `daemon.port`**(只剩 `_SERVICE_PORTS` 别名定义),端口统一彻底。
- **commit**:`1b9c3b3`(P0)/ `4aaaebc`(P1)/ `c488217`(P2)/ `a1612a1`(embed canonical gap + NIT2/3)/ `a076f73`(chroma 校验提取 NIT1)/ `677320a`(三处漏网 canonical gap)。

## 二十三、后端深度审计修复(RBAC 3 P0)+ chroma 拆包 + review 闭合

一份后端深度审计(`AUDIT_REPORT.md`,4 P0+3 P1)逐条核实:**P0-1(rrf_fuse 耦合 jieba)/ P1-1(serve_mcp_diagnose cross_link)已被 §十七 召回重构 + §十五 cross-link 退役解决**(审计时 7 failed 现全 passed,审计是那之前的快照)。剩 3 个代码逻辑 P0(跑测试测不出)修:

- **P0-2 跨 org 边界漏洞**:`user_service.set_roles` 是 6 个写方法里**唯一漏调 `_guard_same_org`** 的 → orgA org_admin 能把 orgB 用户写进 orgA 成员表。最初只补 guard,**review 兄弟揪出 MAJOR**(platform_admin 路径仍可造 orphan membership,admin 跨 org 不校验 `org_id==user.org_id`);终修:改用 `user.org_id` upsert + 校验 org_id 一致性(单一归属模型彻底堵 orphan,含 admin 路径)。
- **P0-3 prod fail-open**:`account_store` 的 `except ImportError` 无条件回退内存(`except Exception` 却有 prod fail-fast)→ prod 配 PG 缺 psycopg 静默走内存丢账户/RBAC 数据。加 prod fail-fast 对齐 + 改测试预期(审计点名的 fail-open 测试)+ 补 dev 回退测试。
- **P0-4 org 归属丢失**:`create_user` 不传 role 就不写 OrgMember → PG 模式 org 从成员表反推落 `'default'`。改默认 `member` 总写。
- P1-2(add_member 不校验 user)审计自述"需求未定"不动;P1-3(session 进程内)是已知 TODO。
- commit:`aa751ed`(3 P0)/ `f2e56d1`(review MAJOR + 3 测试缺口补)。

**chroma/server.py 拆包(审计暴露 669>600 硬约束)**:W2 加 `/embed`//rerank` 撑到 669,`test_file_size_budget` 失败。**两位专家分析**后拆「自洽无环的项目状态机」(规避 `_run_http` 的 `_sse_sessions` 标量重绑/循环 import/`test_health_split` 的 `inspect.getsource` 断言 5 条风险红线):新建 `_project_state.py`(`_ProjectState`+`_projects`+`_ensure_project`/`_load_project_state`/`_maybe_reload_project`/`_project_last_indexed_iso`,只依赖叶子模块不 import server → 无环);server.py re-export 保 `from chroma.server import` 兼容 + 清搬迁后 unused import(`dataclass`/`field`/`Any`/`Path`/`json`/`traceback`,恢复 `time` 给 test_chroma_retry monkeypatch)。**669→512 行达标,file_size_budget 转绿**。验证 `server._ensure_project is _project_state._ensure_project`(同一对象没拆两份)。commit `63660a8`。

**流程教训(记入偏好)**:安全字段(RBAC)+ 重构改动 **commit 前就该先派审计+测试兄弟 review**(偏好 `feedback_use_audit_agent_template`),我跳过了靠用户提醒才补派 —— 而 review 真抓出 `set_roles` 的 MAJOR(我自评"保守安全"实则 admin 路径没堵干净)。补的测试缺口:`test_create_user_respects_explicit_role`(显式 role 不被默认 member 覆盖)/ `test_set_roles_rejects_mismatched_org_id`(MAJOR 回归)/ `tests/test_chroma_project_state.py`(re-export 同一对象 + ast 验无 server import,把"没拆成两份"从手动核查转成断言)。

**验证**:全套本机 **1131 passed**;8 failed 全 pre-existing(W2 recall/embed 测试需 `rank_bm25`/`jieba`/`qwen` 本机 dev 不装)—— **WSL 平台实证这 8 个 `41 passed`**(jieba 在 `.venv/lib/.../jieba/` 在位),坐实是本机缺依赖的环境问题、非代码 bug。
