# 设计:memory 召回流水线解构为可插拔架构(Scorer / Fusion / Reranker + 模型 registry)

> **产物性质**:roadmap-2026-06-05 子设计文档。把 B1 落地的召回逻辑从"子类 + if 开关"重构为
> "固定不变量 + 三段可插拔策略 + 模型 registry",对齐 `agent-provider-architecture.md`(LLM provider
> 按协议族 + registry + config 驱动 + 零 if-else)同一套哲学。
> **一句话**:召回 = `不变量(ACL/redline/去重)` + `[Scorer]→Fusion→Reranker 三段可插拔` +
> `模型走 registry/config`;加能力 / 换模型 = "+1 adapter +1 行注册 + config",核心永不长。

---

## 一、动机 / 问题

B1(`5cef5b0`/`a124179`)落地了向量召回,但形态是 **`LocalRecallService` / `VectorRecallService`
两个子类**,`VectorRecallService._rank` 里把 keyword + vector + RRF 逻辑硬编码在一起。继续往上加
BM25 / reranker 开关 = 在 `_rank` 里堆 `if use_bm25` / `if reranker` 分支 + 模型加载,每加一个能力多
一个分支/子类 → **堆代码,迟早烂**。

用户诉求(明确):**解构、可插拔、可配置、不堆代码;且未来两个 Qwen(嵌入 + 重排)要能换更好的模型。**

## 二、设计原则(复刻 agent-provider-architecture)

1. **固定不变量 vs 可插拔策略分离**:ACL / redline / 去重 / 截断是正确性+安全,留核心、插件不可绕过;
   打分 / 融合 / 重排 / 模型是策略,做成可换插件。
2. **config 驱动 + registry,零 if-else**:选哪些策略、用哪个模型,全由 config 声明;核心遍历注册表,
   不写 `if backend == ...`。
3. **接口稳定**:对外仍是 `RecallService.recall(...)` 同签名 → web 端 / 开发端 / 现有测试零改
   (两端本就共用 `deps.get_recall_service()`)。
4. **模型在接口后**:Embedder / RerankModel 抽象 + registry,换模型 = config + adapter,不动召回逻辑。

## 三、架构:解构后的召回流水线

### 3.1 不变量(留 `PipelineRecallService` 核心,插件碰不到)

```
visible_scopes(ACL)  →  retrieve(PG list_scope)  →  resolve_conflicts(topic_key 去重)
  →  split redline  →  [可插拔三段]  →  redline 永置顶 + top-N 截断
```

### 3.2 三个策略接口 + 两个模型接口

```
Scorer.rank(entries, query, ctx) -> list[str]     # 给候选排个序, 返回 id 序
  ├─ KeywordScorer   子串命中(_score, 零依赖, 现状默认)
  ├─ Bm25Scorer      jieba + rank_bm25(纯 CPU, 复用 chroma/bm25.tokenize)
  └─ VectorScorer    Embedder + chroma 向量索引(query_ids)

Fusion.fuse([list, ...]) -> list[str]             # 多路 → 一路
  └─ RrfFusion       RRF(core.ranking.rrf_fuse;1 路时恒等直通)

Reranker.rerank(query, entries, top_k) -> entries # 可选后置精排
  ├─ NoReranker      恒等(默认)
  └─ QwenReranker    Qwen3-Reranker(经 RerankModel)

# 模型在接口后, registry 按 config 选(镜像 brain/registry.py)
Embedder.encode(text) -> vec        register("qwen-local"/"remote"/"bge"/"openai", ...)
RerankModel.score(query, docs) -> scores   register("qwen-local"/"remote", ...)
```

### 3.3 唯一的 `PipelineRecallService`(取代两个子类)

```python
def recall(...):
    scopes  = visible_scopes(...)               # 不变量
    pool    = self._retrieve(scopes, org_id)     # PG (+ 向量库 if VectorScorer 在列)
    resolved= resolve_conflicts(pool, policy)    # 不变量
    redlines, rest = _split_redline(resolved)    # 不变量
    if not query.strip():                        # 空 query 无信号 → 关键词/recency 兜底
        return _rank_for_query(resolved, query, task_id)[:limit]
    lists   = [s.rank(rest, query, ctx) for s in self._scorers]   # 可插拔
    fused   = self._fusion.fuse(lists)                            # 可插拔
    ordered = _materialize(fused, rest) + _kw_tail(rest, fused)   # 补漏不丢候选
    ranked  = self._reranker.rerank(query, ordered, self._rerank_top_k)  # 可插拔(默认恒等)
    return (_rank_for_query(redlines, query, task_id) + ranked)[:limit]  # 不变量
```

## 四、config schema(声明式驱动)

```jsonc
"memory": {
  "recall": {
    "scorers": ["keyword"],      // local=["keyword"]; vector=["vector","keyword"]; 全套=["vector","bm25"]
    "fusion":  "rrf",            // scorers>1 时生效
    "rerank":  "none",           // "none" | "qwen"
    "rrf_k": 60, "rerank_top_k": 20
  },
  "embed":        { "backend": "qwen-local", "device": "cpu" },   // 换嵌入模型 = 改这
  "rerank_model": { "backend": "qwen-local", "device": "cpu" }    // 换重排模型 = 改这
}
```
旧 `memory.recall_backend: local|vector` 保留为**别名**(`local→["keyword"]`,`vector→["vector","keyword"]`),
不破现状 / 不破现有 WSL 配置。

## 五、模块组织(文件落点)

```
codev_platform/agent/recall/
├── __init__.py
├── service.py        PipelineRecallService(编排 + 不变量;唯一对外类)
├── base.py           Scorer / Fusion / Reranker 抽象(中性类型)
├── registry.py       register_* + build_from_config(遍历注册表, 零 if-else)
├── scorers.py        KeywordScorer / Bm25Scorer / VectorScorer
├── fusion.py         RrfFusion
└── reranker.py       NoReranker / QwenReranker
codev_platform/agent/embed/        # 模型层(Embedder/RerankModel + registry)
├── base.py           Embedder / RerankModel 抽象
└── registry.py       register("qwen-local"/"remote"/...) + build
```
> `recall_service.py` 现有的 `RecallService` ABC / `visible_scopes` / `_score` / `_rank_for_query`
> 保留(被 service.py 复用);`LocalRecallService` / `VectorRecallService` **退役**(行为迁进 pipeline)。
> `memory_vector.py` 的 `Embedder` / `MemoryVectorIndex` 并入 `embed/` + `recall/`。

## 六、分阶段交付

| 阶段 | 内容 | 验证 | 改 GPU? |
|---|---|---|---|
| **P0 骨架重构** | Scorer/Fusion/Reranker 接口 + registry + `PipelineRecallService`;把现有 keyword/vector/RRF **拆进** KeywordScorer/VectorScorer/RrfFusion;`recall_backend` 别名;退役两个子类 | 既有 recall 测试全绿(行为不变)+ pipeline 编排单测(fake scorer) | 否 |
| **P1 BM25 档** | `Bm25Scorer`(jieba+rank_bm25, 复用 chroma/bm25.tokenize)+ register + config `scorers:["vector","bm25"]` | Bm25Scorer 单测 + 编排含 bm25 | 否(纯 CPU) |
| **P2 模型 registry** | `embed/` 层:Embedder/RerankModel 抽象 + registry;`qwen-local` adapter(现状)+ `remote` adapter(走 chroma daemon embed RPC, 复用 GPU 那份, 省第二模型) | embed registry 单测 + remote adapter mock | 解 GPU(remote=复用) |
| **P3 Reranker 档** | `QwenReranker` + RerankModel(优先 `remote` 复用 chroma daemon 已加载的 Qwen3-Reranker)+ config `rerank:"qwen"` | reranker 单测 + 编排含 rerank | 复用(不新增实例) |

**P0 是纯重构(零行为变化),先做;P1 独立可上;P2/P3 绑"共享 RPC"一起(避免再 load GPU 模型,见
[dev-agent-memory-mcp-design] GPU OOM 教训)。**

## 七、迁移 / 兼容(不破接口)

- 对外 `RecallService.recall(...)` 签名不变 → **web 端 + 开发端 + 现有测试零改**。
- `deps.get_recall_service()` 改为 `build_from_config(cfg)` 装配 pipeline;`recall_backend` 旧值映射到
  scorers,旧 config / WSL 现网配置继续工作。
- 不变量(ACL/redline/去重/截断)**上提到 service 核心**,Scorer/Fusion/Reranker **拿不到也改不了**它们
  → 安全不退化(向量库陈旧 / 插件出错都不能泄漏越权或压掉 redline)。

## 八、测试策略(解构后更可测)

- **不变量测试**抽到 pipeline 层、与具体 scorer 无关:redline 永置顶 / ACL 过滤 / 去重前置 / 空 query 退化
  / scorer 异常退回关键词 / 池外 id 忽略(现有 `test_agent_vector_recall.py` 用例迁过来即可)。
- **每个 adapter 独立单测**:KeywordScorer / Bm25Scorer / VectorScorer(fake index)/ RrfFusion /
  QwenReranker(fake model)。
- **registry 测试**:config → 装出预期 pipeline;未知 backend 报错;别名映射。
- **编排测试**:fake scorers 验"多路 → fuse → rerank → 不变量"顺序与裁剪。

## 九、风险 / 不做

- **Reranker / 远程嵌入依赖共享 RPC**:P2/P3 要给 chroma daemon 加 embed/rerank 端点 —— 碰 chroma 服务侧
  (与 W1 chroma 改动有交集),需协调;不单独 load 第二/三个 GPU 模型。
- **不预建**:默认仍 `["keyword"]`(或现网的 vector),BM25/reranker 默认关;**记忆量小时全套零边际收益**
  (四专家共识),给的是"量大了一行 config 开"的杠杆,不是默认全开。
- **不做**:跨项目召回 / 召回结果再喂 LLM 二次加工(那会引入捏造);本设计只动"检索排序",不动消费侧。

## 十、关联

- 哲学母版:`.claude/rules/agent-provider-architecture.md`(协议族 + registry + config + 零 if-else)
- 上游:`dev-agent-memory-mcp-design-2026-06-05.md`(Track M / B1 / GPU OOM 教训)、`next-plan-2026-06-05.md`
- 复用:`core.ranking.rrf_fuse`(RRF)、`chroma/bm25.py:tokenize`(中英混合分词)、
  `chroma/_models.py:_encode_query`(P2 remote adapter 经 RPC 复用)
- 现有实现:`agent/recall_service.py`、`agent/memory_vector.py`、`agent/memory_vector_chroma.py`(待解构)
