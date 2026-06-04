# Daily Summary — 2026-06-05(W2 Track M memory 全栈 + B1 向量召回 / W3 loop-guard 重构)

> 范围:三窗口并行(W1 图谱 / W2 memory / W3 loop-guard)。本文 **W3 段**(§一~§五 + 附:chroma 事故)+ **W2 段**(§六~§十一)。
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
