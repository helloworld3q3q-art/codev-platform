# eval —— 平台能力量化 harness

把"检索 / 代码图谱 / 跨层链路助 AI"这件事**量化成可复跑的数字**。此前平台核心价值零量化;
本 harness 用标准 IR 指标(recall@k / hit@k / MRR / precision@k)给三套能力打分,
对标 supermemory 用 LongMemEval 证明记忆价值的思路,但测的是本平台的代码智能增益。

## 怎么跑

```bash
# 全部 (缺后端的 suite 优雅 skip, 不崩)
.venv/bin/python eval/run_eval.py --suite all

# 单 suite
.venv/bin/python eval/run_eval.py --suite codegraph          # 无 GPU 即可跑
.venv/bin/python eval/run_eval.py --suite memory             # conflict 纯逻辑必跑; recall 需 PG
.venv/bin/python eval/run_eval.py --suite retrieval          # 需 chroma daemon + 模型
.venv/bin/python eval/run_eval.py --suite code_intelligence  # A1/A2 软标签准确率; 需 graph store 有软标签(WSL)
.venv/bin/python eval/run_eval.py --suite planner            # Phase 7 查询分类准确率; 纯确定性, 处处可跑

# 机器可读
.venv/bin/python eval/run_eval.py --suite all --json
```

可选参数:`--project <id>`(覆盖默认 project_id)、`-k <n>`(retrieval top-k,默认 5)。

## 五套 suite + 当前能跑哪些

| suite | 后端 | GPU? | 数据来源 | 默认 project |
|---|---|---|---|---|
| **codegraph** | `codegraph.db` 直查 nodes/FTS | 否,现在即可跑 | 真实符号 ↔ 文件 | `openclaw-stock` |
| **memory** | conflict=纯逻辑 / recall=PG | conflict 否 / recall 需 PG | 记忆冲突消解 + 跨作用域召回 | — |
| **retrieval** | chroma daemon (SSE) | 是,需模型 | 平台自身已索引文档 | `codev-platform` |
| **code_intelligence** | graph store sqlite 直查软边 | 否(读已落库) | A1 业务域 / A2 架构分层软标签 | `codev-platform` |
| **planner** | 无(纯关键词分类) | 否,处处可跑 | 查询类型 golden(Phase 7) | — |

retrieval 需先 `codev-platform serve-mcp start` 拉起 daemon(预热 embedding + reranker ~30-60s);
daemon 没起 / 模型缺 → runner 优雅报"需要什么",不抛异常(`status="skipped"`)。

code_intelligence 把 **A1/A2 软标签准确率验收从人肉核对固化成可回归 golden set**(承 roadmap-2026-06-07
Phase 0 + 06-08 backlog 高 ROI 项):此前 A1 业务域 / A2 架构分层每改一版 prompt(A2 v1→v4)都要人工
重核一遍准确率;本 suite 只**读已落库的软节点/软边**(`PLAYS_ROLE` / `BELONGS_TO_DOMAIN`)对 golden
ground-truth 算分类准确率,**不调 LLM**、纯确定性、可复跑。软标签由 reindex 时的 analyzer 产(config gate
`analyzers.arch_layer.enabled` / `business_domain.enabled`),**上线在 WSL**;本机(如 Windows)没跑过
analyzer → store 无软标签 → `status="skipped"` 并提示怎么开。labeler 是 LLM,但本 suite 测的是其**沉淀
到图谱的结果**,因此可脱离 LLM 回归。

## 指标含义(定义见 `eval/metrics.py`,纯函数全单测)

| 指标 | 含义 |
|---|---|
| `recall@k` | 前 k 个结果命中的相关条目数 / 相关条目总数。衡量"该召回的有没有召回到"。 |
| `hit@k` | 前 k 个里是否至少命中一个相关条目(布尔)。衡量"有没有用"。 |
| `MRR` | 1 / 首个相关命中的排名。衡量"相关结果排得够不够靠前"。 |
| `precision@k` | 前 k 个命中的相关条目数 / k。衡量"返回的有多少是对的"。 |

- codegraph 算 `hit_rate`(期望符号/文件是否被搜到)+ `MRR`(排名)。
- retrieval 算 `recall@5` / `hit@5` / `MRR`。
- memory 算 `resolution_accuracy`(冲突消解胜出条 == 期望,纯逻辑始终可跑)+(有 PG 时)`recall@k` / `hit@k`。
- code_intelligence 算 `arch_role_accuracy` / `business_domain_accuracy`(分类准确率 = golden 文件的实际软标签集**包含**期望标签的比例);detail 区分 `ok`(标对)/ labeled-but-wrong(标错)/ `labeled=False`(漏标),便于定位 prompt 退化是"标错"还是"没标到"。
- planner 算 `classification_accuracy`(`QueryPlanner.classify_query` 把 query 判成 overview/impact/symbol/doc_rule/general 是否等于期望);miss 的 case 会打印出来,便于补关键词表。改分类词表/规则后跑此 suite 即"改前改后"对比(Phase 7 planner 的预算合不合理取决于分类对不对)。

## memory suite(两子集)

对标 supermemory 用 LongMemEval 证明记忆价值的思路,量化本平台记忆的**冲突消解** + **跨作用域召回**:

- **conflict 子集(纯逻辑,始终可跑)**:dataset entries 构造成 `MemoryEntry` → `resolve_conflicts(entries, policy)`
  → 胜出条 content 比 `expect_winner`,算 `resolution_accuracy = 正确/总数`。覆盖:redline 永远胜 /
  personal_first 下 personal 盖 project|team / org_first 下 org 盖 personal|project / supersede 留痕新值胜 /
  同 topic 多条取最高优先级作用域 / 个人 redline 不压 org redline(redline 间按 org 治理序)。
- **recall 子集(需 PG)**:若 `memory.pg_dsn` + psycopg 可用 → 用 `SqlMemoryStore` 把 seed 写进**隔离命名空间**
  (`org_id = "eval-mem-<pid>"`,固定前缀 + pid 保确定性 + 同机并发不撞,绝不碰真数据)→ `LocalRecallService.recall(query)`
  → 算 `recall@k` / `hit@k`(`expect_contains` 关键词命中)→ **try/finally 按 org_id + owner 删净所有 seed**
  (含 supersede 留痕的旧行)。缺 PG / 无 psycopg → recall 子集 `status="skipped"` 带 reason,conflict 子集仍跑。

**清理保证**:recall 子集无论成功 / 抛异常都在 `finally` 删净自己写的行,输出报 `cleaned_rows`;
隔离命名空间从不触碰真业务数据。纯逻辑断言见 `tests/test_eval_memory.py`(不碰 PG)。

## 数据集(`eval/datasets/*.jsonl`)

- `retrieval.jsonl` —— `{query, relevant: [doc 路径]}`,基于平台自己已索引的 rules / docs / plan 主题(可答、有据)。
- `codegraph.jsonl` —— `{query, expect_symbol_or_file}`,基于 codegraph 已索引的真实符号。
- `memory.jsonl` —— 两类:`{kind:"conflict", entries, policy, expect_winner}`(纯逻辑)+ `{kind:"recall", seed, query, expect_contains}`(需 PG;seed 里 `__USER__`/`__PROJ__` 占位由 runner 替成隔离命名空间)。
- `code_intelligence.jsonl` —— `{kind:"arch_role", file, expect_role}`(A2,`expect_role` 取闭集词表 controller/service/repository/domain_model/util/config/adapter/gateway,见 a2 设计 §二)+ `{kind:"business_domain", file, expect_domain}`(A1,数据齐时启用;域名是 LLM 开放命名,需从有数据的 store 取真值后再补 case)。当前 golden 基于 roadmap-2026-06-08 v4 人工验收(`web/routes/*`→controller / `*_pg`→repository / `tables.py`→domain_model / `_checks.py`+`platform_status.py`→service / `codegraph_client.py`+`bridge_codegraph.py`→adapter)。

- `planner.jsonl` —— `{query, expect_type}`(`expect_type` ∈ overview/impact/symbol/doc_rule/general),Phase 7 查询分类 golden。

新增 case 直接往对应 jsonl 追一行即可。`expect_role` 越界词由 `tests/test_eval_code_intelligence.py` 拦。

## agent 抗错误前提(Phase A 验证集, 2026-06-10)

承 `docs/plans/roadmap-2026-06-07/anti-false-premise-plan-2026-06-10.md`(validate-first)。验证苗头
"agent 被问**含错误前提**的问题时顺着编, 而非从证据纠正前提"是不是**真问题**, 而非一上来建机制。

两个**跨 ≥2 项目**(codev-platform + openclaw-stock, 防单仓过拟合)的孪生集:

| 集 | 文件 | 每例前提 | 锚点判法 |
|---|---|---|---|
| **false_premise** | `agent_e2e_false_premise.jsonl`(11 例) | 真为假(已逐例 codegraph 核对, 见 `verified_false`) | `must_mention` 真机制; **禁 must_not**(子串分不清肯定/否定) |
| **control** | `agent_e2e_false_premise_control.jsonl`(11 例) | 同实体但**为真**(隔离"前提为假"单一变量) | 锚点与 false 集**镜像** |

三类错误前提:`nonexistent_capability`(不存在的能力, 如"code_recall 用哪个向量库")/
`changed_old_thing`(已删·改的旧物, 如"audit 还用 open_store 吗")/ `wrong_attribution`
(错误归因, 如"planner 失败会抛异常吗")。结构红线由 `tests/test_eval_false_premise.py` 钉死。

**跑 A3 基线(WSL 步, 需非 deepseek 的非自评 judge)**:

```bash
# false-premise 组 + 对照组, repeat 压小集方差, 非自评 judge(换非被测 provider 名)
.venv/bin/python eval/run_eval.py --suite agent_e2e --e2e-set false_premise \
    --repeat 3 --judge --judge-provider <非被测 provider> --json
.venv/bin/python eval/run_eval.py --suite agent_e2e --e2e-set control \
    --repeat 3 --judge --judge-provider <非被测 provider> --json
```

> ⚠️ **Windows 跑不了**: `get_provider()` 走配置 provider, Windows config 是陈旧影子 → 阻塞;
> agent loop 还需 codegraph/graph/chroma 后端。**A3 一律在 WSL 跑**(见 memory `codev-platform-ops-via-wsl`)。
> 这两集**跨项目**, runner 自动 `cross_project=True` 全集一次跑(不按 `--project` 过滤)。

`metrics.grounding_ci95` 是跨 case 均值的 **bootstrap 95% CI**(确定性 seed, n 小时比点估诚实)。

**Gate A 决策**(走不走 Phase B 机制):
- false-premise 组 grounding **显著低于** control 组(两组 `grounding_ci95` **不重叠**)→ 确证真问题 → Phase B。
- 没差 / CI 重叠 → **停, 记录证伪**(agent 其实不顺前提编), 省 Phase B/C —— 守 `[[recall-weight-ab-finding]]` 纪律。

## TODO scaffold(未做)

- **agent 端到端 eval**(roadmap-2026-06-07 Phase 0 完整版 / Phase 7 配套):给 agent 一个需求 →
  看它是否用对 MCP(MCP adoption)、规划工具、改对文件。需 provider(LLM)+ LLM judge,**移交下一迭代**
  (06-08 backlog)。可复用 `codev_platform/agent/loop.py` + `tools/`。
- **retrieval 用 reranker 前后 A/B**:对比纯向量 vs +BM25 +reranker 的 recall 增益。
- **A1 business_domain golden cases**:`code_intelligence.jsonl` 已支持 `business_domain` kind,但 A1 域名
  是 LLM 开放命名,需从有数据的 store(WSL)dump 真值后补 case(A2 arch_role 已就绪,闭集词表好固化)。

## 入口约定

从简,**不在 CLI 注册子命令**,直接 `python eval/run_eval.py`(README 即说明)。
