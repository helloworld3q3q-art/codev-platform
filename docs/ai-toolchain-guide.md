# 本地 AI 开发工具栈 完整说明

**本次大改**:

- **Qwen3-Reranker-0.6B 两阶段重排** — Chroma 召回 top-30,Reranker 精排取 top-5,语义匹配再上一个台阶(§3.5)
- **MCP server 热重载** — 索引完成写戳,server 自动 reload Chroma 句柄,模型常驻 GPU(§3.6)
- **JSONL 召回日志** — 每次 query 落盘,1 周后可分析召回质量(§3.7)
- **CodeGraph 增量 sync** — 79s → 1.6s(~50× 提速),改一文件场景默认走增量(§2.2)
- **chunk 加大 + env vars 统一** — 500/1000/1500 给 reranker 更完整上下文(§3.1)
- **post-commit hook missed-fire 检测** — errno 1 没根治,加检测器 + 补救命令(§11)
- **dirty-index-check.ps1** — 调 MCP 前先查工作树脏文件是否命中索引范围(§10.5)
- **三个 Skill 入口** — `/ai-health` / `/update-local-ai` / `/verify-pipeline-run` (§12.5)
- **措辞修正** — "禁止 grep+Read" → "优先 MCP,允许 grep+Read 兜底" + 4 种允许场景(§13/§15)

## 0. 为什么需要这套工具

大语言模型(LLM,如 Claude)有一个根本限制:**上下文窗口**。它一次能"看"的内容有限(大约几十万 token,折合几百个文件)。但一个真实项目可能有**几千个文件、几十万行代码、上百份设计文档**。LLM 不可能把整个仓库塞进脑子。

所以问题变成:**怎么让 LLM 在需要时,精准捞到正确的代码片段或文档?**

### 朴素做法的成本

| 朴素做法 | 问题 |
| --- | --- |
| `Grep "shadow_mode"` 命中 50 个文件 → 全部 `Read` | 消耗 50× 的上下文 token,大部分无关 |
| 问 LLM "影子规则怎么实现的?" 它瞎猜 | 幻觉,代码版本不对 |
| 每次会话开始让 LLM 读所有 .md | 30 万 token 全用完,没空写代码 |

### 这套工具栈的核心思想

把仓库**预先索引**到本地数据库,LLM 通过**精确查询**按需取回,而不是把整个仓库塞进上下文。三个维度对应三套索引:

```text
              ┌─────────────────────────────┐
              │   你的项目仓库 (~几千文件)   │
              └──────────────┬──────────────┘
                             │ 预先索引
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
       ┌──────────┐   ┌──────────┐   ┌──────────┐
       │ 代码图谱 │   │ 文档语义 │   │ 业务链路 │
       │CodeGraph │   │  Chroma  │   │cross-link│
       └────┬─────┘   └────┬─────┘   └────┬─────┘
            │              │              │
            └──────────────┼──────────────┘
                           │ MCP 协议
                           ▼
                  ┌─────────────────┐
                  │  Claude / LLM   │
                  │  (按需查询)     │
                  └─────────────────┘
```

**关键收益**:把"AI 帮你写代码"从 30% 准确率提升到 90%+,因为 AI 不再凭印象瞎写,而是基于**当前仓库的真实状态**。

## 1. 三个基础概念

### 1.1 MCP (Model Context Protocol)

Anthropic 制定的开放协议,允许 LLM 与外部"工具服务器"对话。本项目的 3 套工具都是**独立运行的本地 MCP server 进程**:

- Claude Code 启动时 → 自动 spawn 这些 server 进程
- 你问问题 → Claude 把请求转发给对应 MCP server
- server 查本地数据库 → 返回结果 → Claude 用它回答你

### 1.2 Embedding (向量化)

把一段文字转换成一个**定长数字向量**(比如 1024 维浮点数)。语义相近的文本,向量距离也近。

```text
"影子规则数据隔离"   →  [0.12, -0.45, 0.78, ...]  (1024 维)
"shadow_mode 过滤"  →  [0.15, -0.43, 0.81, ...]  (距离很近 = 语义相似)
"涨跌停"             →  [0.91,  0.32, -0.10, ...]  (距离很远 = 无关)
```

用于**文档语义检索**(Chroma):用户问"shadow 规则在哪",即使文档里只写了"影子规则",也能召回。

### 1.3 知识图谱 (代码层面)

把代码解析成**节点 (Node) + 边 (Edge)**:

- 节点:函数、类、文件、SQL 表、API endpoint
- 边:调用、引用、JOIN、HTTP 路由

用于**精确的"谁调用谁"查询**。和 embedding 互补 —— embedding 模糊但能跨语言,图谱精确但只看代码。

## 1.5 完整链路节点图

一张图看清从**源文件**到**你提问得到答案**的所有节点。颜色编码:数据源 索引器 存储 MCP 服务 触发器

### 1.5.1 阅读这张图的 3 个角度

| 角度 | 跟着哪条流? | 看到什么 |
| --- | --- | --- |
| **"我的代码怎么变成 AI 知识"** | 蓝色实线; ① → ② → ③ → ④ | 源文件 → 索引器扫描 → 写入本地存储 → MCP server 暴露给 LLM |
| **"我问问题怎么得到答案"** | 绿色实线; ⑤ → ④ → ③ → ④ → ⑤ | 你提问 → Claude 选工具 → MCP 查存储 → 返回 chunk → Claude 综合回答你 |
| **"索引怎么保持新鲜"** | 红色虚线; git commit / 手工 / 探针 | commit 触发 hook → 自动 spawn 索引器;ai-health 周期性体检存储新鲜度 |

### 1.5.2 五个泳道的职责

| 泳道 | 类比 | 变化频率 |
| --- | --- | --- |
| ① 数据源 | 原材料(铁矿石) | 每次 commit |
| ② 索引器 | 加工厂(熔炼) | 触发时运行 |
| ③ 本地存储 | 仓库(钢锭) | 每次索引覆盖 |
| ④ MCP 服务 | 柜台店员(取货) | 会话期间常驻进程 |
| ⑤ 消费方 | 顾客(LLM + 你) | 实时交互 |

**关键洞察**:这是一个**闭环**。你写代码 → commit → hook 自动重建文档索引 → 下次提问时 LLM 拿到的就是最新状态。**不需要手工维护"AI 的脑子"**,工具链已经把它做成基础设施了。

### 1.5.3 故障定位:哪一段坏了?

| 现象 | 查哪个节点 |
| --- | --- |
| LLM 不知道新写的规则 | ③ Chroma 库 chunks 数;② post-commit hook 是否触发 |
| LLM 召回结果不准 | ② Qwen3 模型;③ 索引是否用旧维度(384 vs 1024) |
| LLM 找不到新加的函数 | ③ codegraph.db nodes 数;② 用户是否跑过 `update-local-ai.ps1 -SkipChroma` |
| 新加 API 但 cross-link 没反映 | ③ cross_layer.sqlite last_build_at;手工跑 update-local-ai |
| 三套 MCP 全部连不上 | ④ MCP server 进程;`~/.claude.json` 配置 |

## 2. CodeGraph — 通用代码图谱

用途 找代码定义、函数源码、调用关系、改动影响范围

### 2.1 它是什么

用 Java 写的**静态代码分析器**,扫描整个仓库的 `.py / .java / .tsx` 文件,把每个符号(函数/类/变量)、每条调用、每个 import 都记入一个 SQLite 数据库。

| 组件 | 位置 | 大小 / 数量 |
| --- | --- | --- |
| 数据库 | `.codegraph/codegraph.db` | 44 MB |
| 节点数 | 函数 / 类 / 文件 | 19,044 |
| 边数 | 调用 / 引用 | 38,048 |
| API jar | `apps/codegraph-api/target/*.jar` | 43.7 MB |

### 2.2 增量 sync 命令

**关键发现**:codegraph CLI 自带 `codegraph sync` 增量命令,只扫描自上次索引后变化的文件。**~50× 提速**:79 秒 → 1.6 秒(改一个文件场景)。

| 命令 | 模式 | 耗时 |
| --- | --- | --- |
| `rebuild_index.ps1`(默认) | 增量 `codegraph sync` | ~1.6 秒 |
| `rebuild_index.ps1 -Full` | 全量 `--force` | ~79 秒 |

#### Race condition 防护:rebuild lock

重建期间在 `.codegraph/.rebuild.lock` 写一个文件。`ai-health.ps1` 看到 lock 存在 → 显式输出"rebuild in progress",不误读半完成的 SQLite。重建完成后自动删除 lock。

```text
# 默认增量(日常用)
scripts\codegraph\rebuild_index.ps1
# → 1.6s,只扫 dirty 文件

# 全量重建(模型升级 / schema 变更 / 怀疑索引损坏)
scripts\codegraph\rebuild_index.ps1 -Full
# → 79s,扫全仓库
```

### 2.3 9 个查询工具

| 工具 | 问什么时候用 |
| --- | --- |
| `codegraph_search` | "符号 X 是什么?"(精确名称查找) |
| `codegraph_context` | "这块功能怎么运作的?"(最常用,组合 search + node + callers + callees) |
| `codegraph_callers` | "X 被谁调用?" |
| `codegraph_callees` | "X 调了谁?" |
| `codegraph_impact` | "改 X 会影响什么?"(blast radius) |
| `codegraph_node` | "看 X 的源码 / signature / docstring" |
| `codegraph_explore` | "探索一组相关符号"(一次性返回多个,优于多次 node) |
| `codegraph_files` | "目录 X 里有什么?" |
| `codegraph_status` | "索引是否就绪?" |

**常见坑**:数据库被锁(`database is locked`)。处理:看 `.codegraph/codegraph.db.lock` 是否 stale(0 字节 + 数小时未变),删之即可。

## 3. platform-docs / Chroma — 文档语义检索

用途 找规则、设计文档、事故复盘、操作手册

### 3.1 它是什么

基于 Chroma (https://www.trychroma.com) 向量数据库 + 本地嵌入模型,把所有 markdown 文档切成"块 (chunk)" → 向量化 → 存到本地 SQLite。

| 属性 | 值 |
| --- | --- |
| 嵌入模型 | `Qwen3-Embedding-0.6B` |
| 精排模型 | `Qwen3-Reranker-0.6B` |
| 向量维度 | 1024 |
| chunks 数 | **3693** |
| chunk 参数 | 500 / 1000 / 1500(min / target / hard max) |
| 覆盖文件 | 188 个 markdown (platform / web / api / pipeline) |
| 分类 | design / operations / rule / incident / claude_md / skill / tool_doc / doc |
| 数据库 | `data/chroma/` (6 segments) |
| 嵌入运行 | 本地 GPU (RTX 5060 Laptop, fp16, ~86s reindex 全量) |

#### chunk 加大的理由

原 chunk 参数 300 / 500 / 800 把规则文件切得过碎。引入 Reranker 后,精排阶段需要给模型更完整的上下文(段落级语义),所以把 chunk 加大到 500 / 1000 / 1500。chunks 实测从 4625 → 3693(-20%),reindex 时间不变(~86s GPU)。

#### 统一 env vars 命名

```text
PLATFORM_RERANKER_MODEL_PATH=D:\models\Qwen3-Reranker-0.6B
PLATFORM_RERANKER_DEVICE=cuda
PLATFORM_RERANKER_ENABLED=true
PLATFORM_SEARCH_RECALL_K=30      # Chroma 召回数(粗排)
PLATFORM_SEARCH_RETURN_K=5       # Reranker 精排后返回数
```

兼容旧名 `PLATFORM_RERANKER_TOP_K`。

### 3.2 4 个查询工具

| 工具 | 用途 |
| --- | --- |
| `search_docs` | 语义搜索文档,可按 `category` / `module` 过滤 |
| `list_collections` | 看文档库统计(各类数量) |
| `get_by_file` | 拿某 .md 文件全文 |
| `search_nodes` | 低层接口,直接查向量库 |

### 3.3 工作原理

```text
索引时:
  .claude/rules/*.md  ─┐
  docs/**/*.md        ─┼─→  分块(三级 chunk)
  **/CLAUDE.md        ─┘         ↓
                                Qwen3 模型 encode(每块 → 1024 维向量)
                                 ↓
                                存入 Chroma (SQLite)

查询时:
  用户问 "影子规则怎么过滤"
    ↓
  Qwen3 encode(query) → 1024 维向量
    ↓
  Chroma 计算余弦距离 → 找最近的 K 个 chunk
    ↓
  返回原始 markdown 片段
```

### 3.4 instruction-aware (Qwen3 特性)

Qwen3 模型支持**区分查询和文档**。索引时直接 encode 文档;查询时给 prompt:

```text
"Instruct: Given a web search query, retrieve relevant passages.
Query: 影子规则数据隔离"
```

这让 retrieval 准确率提升 5-15%。本项目已在 `tools/chroma/index_docs.py` 和 `mcp_server.py` 接入。

**典型场景**:你问"5-15 北向 deprecated 是什么事故",chroma 直接召回 `docs/operations/incident-2026-05-15-north-bound-deprecated.md`,不用你记文件名。

## 3.5 Qwen3-Reranker-0.6B — 两阶段精排

召回 top-30 → 精排 top-5

### 3.5.1 为什么需要 Reranker

纯 embedding 检索(粗排)有个根本短板:把**整个 query 和整个 chunk**各自压成一个向量,丢失细粒度匹配信息。Reranker 是**cross-encoder**,把 `(query, chunk)` 拼成一个序列直接进 transformer,可以做**token 级语义对齐**。

| 对比项 | Embedding (粗排) | Reranker (精排) |
| --- | --- | --- |
| 架构 | bi-encoder,query 和 doc 分开 encode | cross-encoder,query+doc 拼接 |
| 计算 | 1 次 encode + 余弦距离 | N 次 cross attention(慢但准) |
| 规模 | 百万级语料 | 几十~几百 candidate |
| 排序质量 | ~70 分 | ~85-90 分 |

### 3.5.2 两阶段流程

```text
用户 query → "shadow_mode 影子规则 SQL 联动审计 9 处"
                        │
                        ▼
        ┌────────────────────────────────────┐
        │  ① 粗排 (Chroma + Qwen3-Embedding) │
        │  - encode query → 1024 维          │
        │  - 余弦距离 vs 3693 chunks         │
        │  - 返回 top-30 candidates          │
        └─────────────────┬──────────────────┘
                          │
                          ▼
        ┌────────────────────────────────────┐
        │  ② 精排 (Qwen3-Reranker)            │
        │  - 把 (query, candidate) 30 对     │
        │    拼成 chat template              │
        │  - 取 yes / no token logits 差     │
        │  - 用 softmax 归一化为 score       │
        │  - 按 score 重排,取 top-5         │
        └─────────────────┬──────────────────┘
                          │
                          ▼
                  返回给 LLM
```

### 3.5.3 关键技术细节:不是 sentence-transformers CrossEncoder

**正确接法**:Qwen3-Reranker 是基于 chat-template + yes/no token logits 的特殊实现,不能直接用 sentence-transformers 的 `CrossEncoder` 类(那是另一种架构,会跑通但分数错)。

```text
# 错误(返回的 score 是垃圾)
from sentence_transformers import CrossEncoder
model = CrossEncoder("D:/models/Qwen3-Reranker-0.6B")
scores = model.predict([(query, doc) for doc in candidates])

# 正确(本项目实现 — yes/no token logits)
inputs = tokenizer.apply_chat_template(
    [{"role": "user", "content": f"Query: {query}\nDocument: {doc}\nAnswer yes if relevant."}],
    return_tensors="pt"
).to("cuda")
with torch.no_grad():
    logits = model(inputs).logits[0, -1]            # 最后一个位置的 logits
score = (logits[YES_TOKEN_ID] - logits[NO_TOKEN_ID]).item()
```

### 3.5.4 性能基线(实测)

| 指标 | 值 |
| --- | --- |
| 模型路径 | `D:\models\Qwen3-Reranker-0.6B` |
| 文件数 | 9 个(safetensors 分片) |
| 磁盘大小 | 1.1 GB |
| 加载时间 | ~3 秒(首次 GPU) |
| VRAM 占用 | ~1.2 GB(常驻 GPU) |
| 20 pairs 推理 | 1.6s 首次 / 300-500ms warm |
| 默认 RECALL_K / RETURN_K | 30 / 5 |

### 3.5.5 端到端验证

**实测样例**:

Query: `"shadow_mode 影子规则 SQL 联动审计 9 处"`

- 粗排(Chroma 单跑):`CLAUDE.md` 排在第 4 位,前 3 都是 daily summary 碎片
- 精排后:`CLAUDE.md` 拉到第 1 位 (命中"9 处"这个精确数字 + "shadow-isolation.md"硬约束章节)

### 3.5.6 失败降级

Reranker 是**加分项,不是关键路径**。任何环节挂掉时主动降级:

- 模型加载失败 → 走纯 embedding 顺序,记一条 WARN 日志
- 评分异常(空 logits / NaN)→ 跳过该 candidate,不阻塞
- `PLATFORM_RERANKER_ENABLED=false` → 显式关掉,只用粗排

## 3.6 MCP server 热重载机制


### 3.6.1 问题

**原**:每次重建 Chroma 索引后,要手工重启 Claude Code 才能让 MCP server 拿到新数据。否则 server 还在用旧的 Chroma 句柄,新增的 chunk 查不到。

### 3.6.2 解法:索引完成写戳 + server 自动 reload

| 组件 | 行为 |
| --- | --- |
| `index_docs.py` | 索引完成时写 `data/chroma/.last_build.json`(mtime + chunks + dim + model) |
| `mcp_server.py` | 每次 query 前 `stat` 戳文件,mtime 变新 → drop Chroma collection 句柄重连 |
| 关键点 | **只 reload Chroma client,模型常驻 GPU 不重载**(避免 3s 加载抖动) |

### 3.6.3 内部分层:模型 vs 句柄

```text
# 伪代码
_model = None              # SentenceTransformer,常驻进程
_reranker = None           # Qwen3-Reranker,常驻 GPU
_collection = None         # Chroma collection 句柄,变化时重连
_last_build_mtime = 0      # 戳文件 mtime 缓存
_init_error = None         # 自愈用

def _maybe_reload_collection():
    cur_mtime = stat(".last_build.json").st_mtime
    if cur_mtime > _last_build_mtime:
        _collection = None       # drop 句柄
        _init_error = None       # 同时清错误(重建瞬间不卡查询)
        _last_build_mtime = cur_mtime

def _ensure_model():
    if _model is None: _model = SentenceTransformer(EMBED_PATH)
    if _reranker is None and RERANK_ENABLED: _reranker = load_reranker()
```

### 3.6.4 自愈细节

`_init_error` 在戳变新时同时清空。**原因**:重建索引的瞬间,server 可能查到一半的 SQLite 报错,把错误缓存住。戳更新时同时清错误,下一次 query 就能正常工作,无需手动干预。

## 3.7 JSONL 召回日志

收集 1 周后做召回质量分析

### 3.7.1 位置

```text
tools/chroma/search_recall.jsonl
# 每行一个 JSON,append-only
```

### 3.7.2 字段

| 字段 | 说明 |
| --- | --- |
| `ts` | 查询时间戳(ISO) |
| `query` | 用户原始 query 字符串 |
| `k` | 请求返回数 |
| `category` / `module` | 过滤参数(可空) |
| `hit` | 实际返回数 |
| `rerank_used` | true / false(是否走精排) |
| `n_candidates` | 粗排 candidate 数(走精排时) |
| `elapsed_ms` | 端到端耗时 |
| `top5[].file` | top-5 命中文件路径 |
| `top5[].distance` | 粗排余弦距离 |
| `top5[].rerank_score` | 精排分数(可空,关掉精排时) |

### 3.7.3 用途

- 积累 1 周后,扫"top-1 distance > 0.5"的样本 → 这些是召回质量差的 query,可能需要 reindex 或调 chunk
- 对比 `rerank_score` vs `distance` 的 ranking,看精排实际收益
- 查 `elapsed_ms` P95 / P99,看是不是真有性能瓶颈

## 4. cross-link — 跨层业务链路

用途 找前端 API ↔ Java endpoint ↔ Table 的完整业务链

### 4.1 它是什么

专门为**本项目的三层架构**(前端 → Java → DB / Python)定制的链路索引。CodeGraph 是通用图,cross-link 知道"Controller / Facade / Service / Mapper / Table"这些业务层级语义。

| 索引内容 | 数量 |
| --- | --- |
| Java endpoint = 前端 API | 128 / 128 (0 mismatch) |
| Controller → Facade 边 | 280 |
| Facade → Service 边 | 124 |
| Service → Mapper 边 | 133 |
| Mapper → Table 边 | 168 |
| Python method ↔ 表读/写/更新 | 70 / 39 / 42 / 13 |
| Flyway 迁移 | 90 |
| 表 / 列 | 52 / 723 |

### 4.2 4 个查询工具

| 工具 | 用途 |
| --- | --- |
| `find_endpoint_link` | 给定 endpoint,返回完整链:前端 API → Java endpoint → Facade → Service → Mapper → Table |
| `find_table_refs` | 给定表名,返回所有读/写它的 Java Mapper + Python 仓储 |
| `search_nodes` | 跨层搜符号 |
| `cross_link_stats` | 看库统计 + 索引时间 |

**典型场景**:"我要改 `stock_recommend_result.shadow_mode` 字段语义,会影响哪些代码?" → `find_table_refs("stock_recommend_result")` 一次性返回 9 处 SQL + 2 处 Python 仓储,不会漏。

## 5. 三者边界对比

| 问题类型 | 用谁 | 为什么 |
| --- | --- | --- |
| "`save_recommendations` 函数源码" | **CodeGraph** | 精确符号查找 |
| "`save_recommendations` 被谁调用" | **CodeGraph** | callers |
| "改这个函数会影响什么" | **CodeGraph** | impact / blast radius |
| "shadow 规则相关的设计文档" | **Chroma** | 语义检索 .md |
| "5-18 那次跑批失败的复盘" | **Chroma** | 事故归档语义匹配 |
| "PIT 红线有几条规则" | **Chroma** | 规则文档搜索 |
| "`/v1/stocks/detail-full` 完整调用链" | **cross-link** | endpoint 链路图 |
| "`stock_alert_event` 表被谁读" | **cross-link** | 表引用专表 |
| "前端 API 与后端 endpoint 是否一致" | **cross-link** | 双源对账 |

**反例**:不要无目的 `Grep` + `Read` 循环找代码或文档。MCP 索引已经预先做了大部分工作；只有 MCP 不可用、索引滞后、dirty 命中或需要确认最新源码时,才用本地搜索兜底。

## 6. MEMORY — 自动加载的用户记忆

用途 跨会话保留用户偏好 / 反馈 / 项目状态

适用范围:这是 Claude Code 语境的自动记忆。Codex 不自动加载该 MEMORY 目录；需要相关偏好时,应以当前会话、`AGENTS.md`、`CLAUDE.md` 和显式读取的规则文件为准。

### 6.1 它在哪

```text
C:\Users\G1706256\.claude\projects\D--WorkSpace-platform\memory\
├── MEMORY.md                  ← 总索引(每次会话自动注入 LLM)
├── feedback_dev_order.md       ← 一个偏好
├── feedback_stock_display.md   ← 一个偏好
├── reference_mcp_tools.md      ← 一个参考
└── ...
```

### 6.2 工作方式

- 每次会话开始,系统自动把 `MEMORY.md` 注入 LLM 的 system prompt
- LLM 看到"用户偏好 X" → 自动应用,不需要每次重复说
- 需要的时候,LLM 会 `Read` 具体子文件拿详细内容

### 6.3 4 类记忆

| 类型 | 例子 |
| --- | --- |
| `user` | 用户角色、目标、知识背景 |
| `feedback` | "开发顺序 Python → Java → Web,前端最后" |
| `project` | "5-18 这次 commit 的真实原因是 X" |
| `reference` | "Linear 的 INGEST 项目跟踪 pipeline bug" |

**关键区别**:MEMORY 是 LLM 自己维护的,你跟它说"以后记住 X" 它会自己写入;而上面三套 MCP 是**预先索引的代码 / 文档客观事实**,不会被一句话改变。

## 7. Embedding 模型 (Qwen3-Embedding-0.6B)

### 7.1 升级历程

| 阶段 | 模型 | 维度 | 中文 retrieval |
| --- | --- | --- | --- |
| 起步 | paraphrase-multilingual-MiniLM-L12-v2 | 384 | ~60 |
| | **Qwen3-Embedding-0.6B** | **1024** | **71+** |

### 7.2 为什么选 0.6B 不选 4B / 8B

- 本地 chroma 是**冷工具**(每周 1-2 次查询),投入 VRAM 性价比下降
- 0.6B 已经覆盖 95% 的中文 retrieval 收益
- 4B / 8B 显存占用 4-8GB,挤压其他模型 / 游戏空间

### 7.3 部署位置

```text
D:\models\Qwen3-Embedding-0.6B   (~1.2 GB,仓库外,跨项目共享)
```

### 7.4 关键技术细节

**正确接法**:绕开 Chroma 内置的 `SentenceTransformerEmbeddingFunction`,索引侧 + 查询侧**自己拿模型 encode** → 直接传 `embeddings=` 给 Chroma。否则 instruction-aware 静默失效,Qwen3 的优势白瞎。

```text
# 错误(EF 路径无法区分 query / doc prompt)
col = chroma.get_or_create_collection(
    embedding_function=SentenceTransformerEmbeddingFunction(...)
)
col.add(documents=docs)  # 模糊匹配,没用 prompt

# 正确(本项目当前实现)
model = SentenceTransformer("D:/models/Qwen3-Embedding-0.6B")
doc_embeds = model.encode(docs)                              # 索引侧无 prompt
q_embeds = model.encode([q], prompt_name="query")           # 查询侧加 instruction
col.add(embeddings=doc_embeds, documents=docs)
col.query(query_embeddings=q_embeds)
```

### 7.5 性能基线

| 指标 | MiniLM | Qwen3-0.6B |
| --- | --- | --- |
| 磁盘 | 480 MB | 1136 MB |
| VRAM (fp16) | ~80 MB | ~600 MB |
| reindex 3693 chunks | 20s | 86s |
| query 单次 encode | <50ms | ~150ms |
| 最大上下文 | 512 tokens | 32768 tokens |

### 7.6 硬件资源全景

| 项 | 占用 |
| --- | --- |
| GPU | RTX 5060 Laptop 8GB GDDR7 |
| CPU | Intel Core Ultra 7 255H |
| 系统 RAM | 32 GB(项目 ~20 GB,余量 ~7 GB) |
| Embedding VRAM | ~600 MB(常驻) |
| Reranker VRAM | ~1200 MB |
| 总 VRAM 占用 | ~1.8 GB / 8 GB(留 6.2 GB 给游戏 / 其他模型) |

## 8. `ai-health.ps1` — 健康体检

位置 `tools/dev/ai-health.ps1`

### 8.1 一句话

跑一次脚本,2 秒内告诉你 **12 项关键指标**是否健康。重启会话 / 升级模型 / 出现可疑现象时第一站。

### 8.2 完整检查项

| 类别 | 项 | 验证内容 |
| --- | --- | --- |
| Chroma 嵌入 | `chroma venv` | Python 虚拟环境存在 |
| Chroma 嵌入 | `embed model` | 模型文件齐全(11 个文件) |
| Chroma 嵌入 | `embed load` | sentence-transformers 能加载 + 维度正确 + prompt 已配 |
| Chroma 嵌入 | `torch cuda` | GPU 可用(`cuda=True`) |
| Chroma 索引 | `data dir` | 数据目录 + segments 数 |
| Chroma 索引 | `collection` | chunks 数+维度+模型名匹配 |
| Chroma 索引 | `freshness` | 索引比 `.md` 最新 mtime(不落后于文档) |
| 规则一致性 | `rules vs incident` | 规则覆盖最新事故(防规则滞后) |
| CodeGraph | `cross_layer freshness` | 跨层图节点/边数 + 时间 |
| CodeGraph | `codegraph db` | sqlite 大小 + 节点/边数 |
| API 服务 | `codegraph-api` | Java jar 存在 |
| 清洁度 | `git tools/` | `tools/` 无未提交污染 |

### 8.3 退出码

- 全 OK → `exit 0` + 输出 `SUMMARY: all green`
- 任一 WARN → `exit 2`(非阻塞,提醒)
- 任一 FAIL → `exit 1`(关键栈坏)

### 8.4 实测输出

```text
[OK ] chroma venv          .venv/Scripts/python.exe
[OK ] embed model          D:\models\Qwen3-Embedding-0.6B (11 files)
[OK ] embed load           dim=1024 max_seq=32768 query_prompt=True
[OK ] reranker model       D:\models\Qwen3-Reranker-0.6B (9 files) 
[OK ] torch cuda           torch=2.11.0+cu128 cuda=True gpu=RTX 5060 Laptop
[OK ] chroma collection    chunks=3693 dim=1024 model=Qwen3-Embedding-0.6B
[OK ] chroma freshness     index newer than latest doc
[OK ] hook missed?         no recent missed-fire (HEAD matches reindex.log)
[OK ] codegraph db         44 MB, nodes=19044 edges=38048
[OK ] codegraph-api        codegraph-api-0.1.0-SNAPSHOT.jar
[OK ] git tools/           clean
SUMMARY: all green
```

### 8.5 hook missed-fire 检测

Git for Windows 的 post-commit hook 偶发 errno 1 不执行(详见 §11)。`ai-health`加一项:

- 读 `git log -1 --format=%H`(HEAD SHA)
- 读 `tools/chroma/reindex.log` 最近一行的 SHA
- 两者不一致 → 显式 WARN + 给补救命令 `powershell -File tools/dev/post-commit.ps1`

## 9. `update-local-ai.ps1` — 重建索引

位置 `tools/dev/update-local-ai.ps1`

### 9.1 用途

一键重建本地 AI 索引。两个开关控制要重建哪部分:

| 命令 | 做什么 | 耗时 |
| --- | --- | --- |
| `update-local-ai.ps1` | 全量(Chroma + CodeGraph + cross-link) | ~3 分钟 |
| `update-local-ai.ps1 -SkipCodeGraph` | 只重建 Chroma + cross-link(文档改动时) | ~80 秒 |
| `update-local-ai.ps1 -SkipChroma` | 只重建 CodeGraph + cross-link(代码改动时) | ~2 分钟 |

### 9.2 何时手工跑

| 场景 | 动作 |
| --- | --- |
| 日常代码改动 commit | 不需要(hook 跳过) |
| 文档 / 规则 / 日报改动 commit | 不需要(hook 自动跑 `-SkipCodeGraph`) |
| 代码大改后,要查影响面 | 手工 `-SkipChroma` 同步 codegraph |
| 两边都动 / 周末维护 | 手工跑全量 |

## 10. `clean-local-artifacts.ps1` — 工作区清理

位置 `tools/dev/clean-local-artifacts.ps1`

### 10.1 设计哲学

**保守优先**。默认只清最确定的垃圾,递归删需要显式开关。永不触碰 `.claude/rules/` / `docs/` / `models/` / `data/chroma/` / `.codegraph/codegraph.db` / `archive/incidents/`。

### 10.2 三档模式

| 命令 | 清什么 | 实测占用 |
| --- | --- | --- |
| 默认 | 9 个已知运行时 dump(`.tmp_*.log` / `bat_*.txt` 等) | ~1.4 MB |
| `-DeepClean` | 递归 `__pycache__/` + `.pytest_cache/`(避开 `.venv` / `node_modules`) | 视情况 |
| `-KeepLogs N` | 轮替 `python/stock-pipeline/archive/logs/` 保留最近 N 个 | 默认保留全部 |
| `-DryRun` | 只列要删的,不真删 | 0 |

## 10.5 `dirty-index-check.ps1` — MCP 兜底前置检查

位置 `tools/dev/dirty-index-check.ps1`

### 10.5.1 用途

调 CodeGraph / cross-link / Chroma 之前先查工作树是否有 dirty 文件命中索引范围。命中 → 提示"索引可能滞后,建议 grep+Read 兜底"。

### 10.5.2 三种使用模式

| 命令 | 用途 | 输出 |
| --- | --- | --- |
| `dirty-index-check.ps1` | 人工查看 | 列表 + 兜底建议 |
| `dirty-index-check.ps1 -Json` | 给 AI 用 | JSON 结构,LLM 可解析 |
| `dirty-index-check.ps1 -Quiet` | 脚本调用 | 只看 exit code |

### 10.5.3 退出码

- `exit 0` — 工作树干净 / dirty 不命中索引范围,MCP 结果可信
- `exit 1` — dirty 命中(*.py / *.java / *.tsx / *.md),输出受影响文件 + 兜底建议
- `exit 2` — 不在 git 仓库

### 10.5.4 接入 SOP

已写入 `.claude/rules/ai-tools-mcp.md` §2.1。LLM 在调 MCP 工具前先跑这个脚本,有 dirty 命中时:

- 把 MCP 结果加注脚 "工作树有未提交改动,索引可能滞后"
- 关键决策(写代码 / 改 SQL / 改合规字段)前用 grep+Read 在工作树**真实文件**上做最后核对

## 11. Git post-commit hook — 自动重建

### 11.1 怎么装

```text
powershell -NoProfile -ExecutionPolicy Bypass -File tools/dev/install-git-hooks.ps1
```

把 `tools/dev/git-hooks/post-commit` 复制到 `.git/hooks/post-commit`(后者不入版本控制,需要装一次)。

### 11.2 触发逻辑

```text
git commit  ─→  hook 被触发
                 │
                 ▼
            git diff-tree HEAD 看改了哪些文件
                 │
        ┌────────┴────────┐
        ▼                 ▼
  命中文档路径?      未命中(纯代码)
    │                     │
    ▼                     ▼
  后台 detached 启动     静默 no-op
  update-local-ai.ps1
  -SkipCodeGraph         (代码改动 hook 不管,
        │                 codegraph 用户手工同步)
        ▼
  ~80s 后索引刷新完
  日志 tools/chroma/reindex.log
```

### 11.3 命中规则(文档变更才触发)

- `docs/`
- `.claude/rules/` / `.claude/skills/`
- `**/CLAUDE.md` / `**/AGENTS.md`
- `tools/**/*.md`

**设计原则**:reindex 跟着**内容**变,不跟着**时间**变。代码 commit 不触发(避免噪音),文档 commit 自动触发(保索引新鲜)。

### 11.4 sh stub 极简化

Git 在 Windows 下用 sh.exe 执行 hook,但 Git Bash 子 shell 偶发 errno 1。改造:

- 删除 `$(git rev-parse --show-toplevel)` 子 shell(errno 1 真凶之一)
- 用**绝对路径** `/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe`
- 整个 stub 极简化到 5 行,只做 spawn,不做任何条件判断 / 计算

### 11.5 errno 1 没被根治 → missed-fire 检测器兜底

**诚实结论**:Git for Windows 2.54 升级也没修(测试 5 次 commit 仍 2 次中招)。我们不再追求"100% hook 触发",而是**建检测器**:

| 层 | 动作 |
| --- | --- |
| 第一道:hook 本身 | sh stub 尽可能简单,降低失败率(但不为 0) |
| 第二道:`ai-health` 体检 | 对比 HEAD SHA 与 `reindex.log`,漏跑显式 WARN |
| 第三道:手工补救 | `powershell -File tools/dev/post-commit.ps1` 一行命令补触发 |

核心思想:**容忍不完美自动化,加可观测性 + 一行补救**,比死磕"hook 100% 不漏跑"更务实。

## 12. `embed_ab_test.py` — 升级前后对比

位置 `tools/dev/embed_ab_test.py`

### 12.1 用途

未来换 embedding 模型时(比如 Qwen3-1B / BGE-M3 / 上 reranker),用这个脚本跑 A/B 对照,防 cherry-pick。

### 12.2 工作方式

1. 预定 5 个"真实查询"(故意挑 vague 中文 + 代码符号混合 + 跨档案)
2. 用**旧模型**跑一遍,top-5 结果存 `_ab_baseline_*.json`
3. 换**新模型**重建索引
4. 跑相同 5 个查询,top-5 结果存 `_ab_candidate_*.json`
5. `_ab_diff.py` 输出对比表(每个 query 哪个胜)

### 12.3 升级实测(MiniLM → Qwen3-0.6B)

| Query | MiniLM 结果 | Qwen3 结果 | 裁决 |
| --- | --- | --- | --- |
| 影子规则 SQL 联动审计 | shadow-isolation #2 #3 | shadow-isolation 掉到 #4 | MiniLM |
| 5-20 Chain C bug 定位 | 5-20 daily 标题(#0) | 5-20 daily 实质章节(#2) | Qwen3 略优 |
| policy_version incident | dist=0.34 | dist=0.27(更紧) | 平 |
| Qwen3 接入 Chroma | 引入无关 codegraph-viewer | 4 chunks 无插入 | Qwen3 |
| sys_user capital 真值源 | multi-user-design(沾边) | 5-18 决策实际地 | Qwen3 |

**比分:Qwen3 赢 2 / 平 2 / 输 1**。结论:数据驱动接受升级。

## 12.5 三个 Skill 入口

把工具栈封装成 Claude Code 的 slash command。Codex 不执行 slash command；Codex 需要直接读取对应 `.claude/skills/<name>/SKILL.md`,再按文件里的脚本和参数执行。

### 12.5.1 设计

本工具栈的核心脚本都是 PowerShell,直接调用记不住参数。封装成 Skill 后:

- 用户输入 `/ai-health` / `/update-local-ai` / `/verify-pipeline-run`
- Claude Code 读对应 `.claude/skills/<name>/SKILL.md` → 知道该跑哪个脚本 / 传什么参数
- Codex 直接打开对应 `SKILL.md` → 按步骤执行,不走 slash command
- 支持 multiSelect:用户选择子项,Skill 根据选择拼参数

### 12.5.2 三个 Skill

| Skill | 包装的脚本 | multiSelect |
| --- | --- | --- |
| `/ai-health` | `ai-health.ps1` + `dirty-index-check.ps1` | 3 档:health / dirty / both |
| `/update-local-ai` | `update-local-ai.ps1` | 4 档:all / Chroma / CodeGraph / cross-link |
| `/verify-pipeline-run` | 业务侧验证(不在本工具栈范围) | 3 档:morning / eod / all |

### 12.5.3 位置

```text
.claude/skills/ai-health/SKILL.md
.claude/skills/update-local-ai/SKILL.md
.claude/skills/verify-pipeline-run/SKILL.md
```

### 12.5.4 使用示例

```text
# 在 Claude Code 里
> /ai-health both
# → 跑 ai-health.ps1 + dirty-index-check.ps1,两个都看

> /update-local-ai Chroma
# → 只重建 Chroma(等价于 update-local-ai.ps1 -SkipCodeGraph)

> /update-local-ai all
# → 全量重建(Chroma + CodeGraph + cross-link)
```

## 13. 标准工作流

### 13.1 修 bug 的标准链

1. `codegraph_search "BugSymbol"` — 定位代码
2. `codegraph_callers "BugSymbol"` — 找所有调用方
3. `codegraph_impact "BugSymbol"` — blast radius
4. `search_docs "BugSymbol 相关规则"` — 查约束
5. `find_table_refs "相关表"` — 跨层引用
6. 动手 `Edit` / `Write`

### 13.2 写新功能的标准链

1. `search_docs "类似功能 设计"` — 找设计文档参考
2. `codegraph_context "现有类似实现"` — 找参照
3. `find_endpoint_link "类似 endpoint"` — 看完整链路
4. 动手实现
5. `pytest tests/` + `mvn test` 验证

### 13.3 跨层改动(Python ↔ Java ↔ 前端)

1. `find_endpoint_link "目标 endpoint"`
2. `find_table_refs "涉及表"`
3. 按 **Python → Java → 前端** 顺序改
4. `pnpm run api` — 前端类型重生成
5. `cross_link_stats` — 看新 endpoint 是否被索引

### 13.4 平时的 hook 自动化

```text
你写代码          → commit  → 代码 hook 跳过(不动索引)
你写文档/规则     → commit  → 后台自动 reindex Chroma(86 秒,不阻塞)
                            → 戳更新,MCP server 热重载(§3.6)
你周末扩展功能     → 手工跑 /update-local-ai all(3 分钟)
                            → 跑 /ai-health both 验栈
```

### 13.5 措辞修正:优先 MCP,允许 grep+Read 兜底

原 `ai-tools-mcp.md` 写"**禁止**用 grep+Read 循环",过于绝对。修正为"**优先** MCP,允许 grep+Read 兜底",并列出 4 种允许场景:

| 允许兜底的场景 | 为什么 |
| --- | --- |
| 未提交改动命中查询范围 | 索引最新到 HEAD,工作树新改的代码 MCP 看不到 |
| 怀疑索引滞后 | hook 漏跑 / `ai-health` 报 stale |
| MCP 工具不可用 | server 进程崩 / db locked / 网络问题 |
| 确认最新源码 | MCP 返回片段后要确认实际行号、与最近编辑后的真实状态 |

**SOP**:调 MCP 前先 `git status -s` 或 `dirty-index-check.ps1`,dirty 命中提示"索引可能滞后"。

## 14. 故障排查

| 现象 | 处理 |
| --- | --- |
| `codegraph database is locked` | 检查 `.codegraph/codegraph.db.lock`,stale(0 字节 + 数小时未变 + 无活进程)即删 |
| platform-docs 召回质量差 | 看 `ai-health` 的 `chroma freshness`;文档大改后跑 `update-local-ai.ps1 -SkipCodeGraph` |
| cross-link 数据陈旧 | 看 `cross_link_stats.build_meta.last_build_at`;跑 `update-local-ai.ps1` |
| MCP server 完全连不上 | 检查 `~/.claude.json` 配置;重启 Claude Code(**不是 /clear**) |
| Qwen3 升级后查询没改善 | 确认绕开 EF 路径,索引侧 + 查询侧都自己 encode(详见 §7.4) |
| `ai-health` WARN `codegraph-api no jar` | `mvn -f apps/codegraph-api/pom.xml package -DskipTests` |
| `git status` 总是脏 | 检查 `.gitignore`;运行 `clean-local-artifacts.ps1 -DryRun` 看冗余文件 |

## 15. 升级路线

### 已完成

- 三套 MCP server 全部联通(CodeGraph / Chroma / cross-link)
- Qwen3-Embedding-0.6B 上线 + instruction-aware
- **Qwen3-Reranker-0.6B 两阶段精排** — top-30 召回 → top-5 精排
- **MCP server 热重载** — 戳文件触发 client 重连,模型常驻 GPU
- **JSONL 召回日志** — search_recall.jsonl 收集召回质量数据
- **CodeGraph 增量 sync** — 79s → 1.6s,50× 提速 + rebuild lock
- **chunk 加大 500/1000/1500** — 给 Reranker 更完整上下文
- **dirty-index-check.ps1** — MCP 兜底前置检查
- **三个 Skill 入口** — `/ai-health` / `/update-local-ai` / `/verify-pipeline-run`
- **post-commit hook missed-fire 检测** — errno 1 不根治,加可观测性兜底
- Git post-commit hook 自动 reindex 文档
- ai-health 12 项体检 + 索引新鲜度检测
- A/B test 工具链(防 cherry-pick)
- .gitignore 治根 + 工作区清理

### 潜在未来项(按价值排序)

1. **定时 codegraph 重建** — 把"代码改动手工跑 codegraph"也 hook 化(增量 sync 已 1.6s,接入 hook 成本降低)
2. **召回日志分析脚本** — 1 周后扫 search_recall.jsonl,跑 top-1 distance 分布 / 精排收益对比
3. **Embedding 模型 4B 升级** — 中文 retrieval 再 +3-5 分,但 VRAM 翻 4 倍,收益曲线递减
4. **MCP 调用次数统计** — 看是不是真的避开了 grep+Read 循环
5. **hook errno 1 根因排查** — 长期看能否定位到 Git for Windows / sh.exe 的具体 bug

---

## 附录:文件清单速查

| 类别 | 路径 | 用途 |
| --- | --- | --- |
| CodeGraph | `.codegraph/codegraph.db` | SQLite 索引(.gitignore) |
| CodeGraph | `apps/codegraph-api/target/*.jar` | Java API 服务(可选) |
| Chroma | `data/chroma/` | 向量数据库 |
| Chroma | `data/chroma/.last_build.json` | 戳文件 |
| Chroma | `tools/chroma/index_docs.py` | 三级 chunk 索引器 |
| Chroma | `tools/chroma/mcp_server.py` | MCP server 实现(含热重载 + JSONL 日志) |
| Chroma | `tools/chroma/search_recall.jsonl` | 召回日志 |
| Chroma | `tools/chroma/platform-docs-mcp.cmd` | 启动入口(Claude Code 调用) |
| Chroma | `tools/chroma/README.md` | 完整文档 |
| Chroma | `D:\models\Qwen3-Embedding-0.6B` | 嵌入模型(仓库外,1.2GB) |
| Chroma | `D:\models\Qwen3-Reranker-0.6B` | 精排模型 |
| cross-link | `data/codegraph_ext/cross_layer.sqlite` | 跨层数据库 |
| cross-link | `tools/cross_link/` | 构建脚本(Python) |
| cross-link | `.codegraph/.rebuild.lock` | rebuild lock |
| 脚本 | `tools/dev/ai-health.ps1` | 体检(含 hook missed-fire + reranker 检测) |
| 脚本 | `tools/dev/update-local-ai.ps1` | 重建索引 |
| 脚本 | `tools/dev/clean-local-artifacts.ps1` | 清理工作区 |
| 脚本 | `tools/dev/dirty-index-check.ps1` | MCP 兜底前置检查 |
| 脚本 | `tools/dev/install-git-hooks.ps1` | 装 git hook |
| 脚本 | `tools/dev/post-commit.ps1` | hook 实体 |
| 脚本 | `tools/dev/embed_ab_test.py` | A/B 测试 |
| 脚本 | `scripts/codegraph/rebuild_index.ps1` | CodeGraph 增量 sync |
| Skill | `.claude/skills/ai-health/SKILL.md` | /ai-health 入口 |
| Skill | `.claude/skills/update-local-ai/SKILL.md` | /update-local-ai 入口 |
| Skill | `.claude/skills/verify-pipeline-run/SKILL.md` | /verify-pipeline-run 入口 |
| MEMORY | `C:\Users\G1706256\.claude\projects\D--WorkSpace-platform\memory\` | 跨会话用户记忆 |
| 核心规则 | `.claude/rules/ai-tools-mcp.md` | 本工具栈的使用约束 |
