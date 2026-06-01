# eval —— 平台能力量化 harness

把"检索 / 代码图谱 / 跨层链路助 AI"这件事**量化成可复跑的数字**。此前平台核心价值零量化;
本 harness 用标准 IR 指标(recall@k / hit@k / MRR / precision@k)给三套能力打分,
对标 supermemory 用 LongMemEval 证明记忆价值的思路,但测的是本平台的代码智能增益。

## 怎么跑

```bash
# 全部 (缺后端的 suite 优雅 skip, 不崩)
.venv/bin/python eval/run_eval.py --suite all

# 单 suite
.venv/bin/python eval/run_eval.py --suite crosslink     # 无 GPU 即可跑
.venv/bin/python eval/run_eval.py --suite codegraph     # 无 GPU 即可跑
.venv/bin/python eval/run_eval.py --suite retrieval     # 需 chroma daemon + 模型

# 机器可读
.venv/bin/python eval/run_eval.py --suite all --json
```

可选参数:`--project <id>`(覆盖默认 project_id)、`-k <n>`(retrieval top-k,默认 5)。

## 三套 suite + 当前能跑哪些

| suite | 后端 | GPU? | 数据来源 | 默认 project |
|---|---|---|---|---|
| **crosslink** | `cross_layer.sqlite` 直查 | 否,现在即可跑 | 真实 endpoint↔table↔writer/reader 链路 | `openclaw-stock` |
| **codegraph** | `codegraph.db` 直查 nodes/FTS | 否,现在即可跑 | 真实符号 ↔ 文件 | `openclaw-stock` |
| **retrieval** | chroma daemon (SSE) | 是,需模型 | 平台自身已索引文档 | `codev-platform` |

retrieval 需先 `codev-platform serve-mcp start` 拉起 daemon(预热 embedding + reranker ~30-60s);
daemon 没起 / 模型缺 → runner 优雅报"需要什么",不抛异常(`status="skipped"`)。

## 指标含义(定义见 `eval/metrics.py`,纯函数全单测)

| 指标 | 含义 |
|---|---|
| `recall@k` | 前 k 个结果命中的相关条目数 / 相关条目总数。衡量"该召回的有没有召回到"。 |
| `hit@k` | 前 k 个里是否至少命中一个相关条目(布尔)。衡量"有没有用"。 |
| `MRR` | 1 / 首个相关命中的排名。衡量"相关结果排得够不够靠前"。 |
| `precision@k` | 前 k 个命中的相关条目数 / k。衡量"返回的有多少是对的"。 |

- crosslink 用**全量召回**(k=命中列表长度)算 `recall` / `hit_rate`,衡量"期望的跨层链路是否都被索引到"。
- codegraph 算 `hit_rate`(期望符号/文件是否被搜到)+ `MRR`(排名)。
- retrieval 算 `recall@5` / `hit@5` / `MRR`。

## 数据集(`eval/datasets/*.jsonl`)

- `retrieval.jsonl` —— `{query, relevant: [doc 路径]}`,基于平台自己已索引的 rules / docs / plan 主题(可答、有据)。
- `crosslink.jsonl` —— `{kind: table|endpoint, key, expect_contains: [...]}`,基于 openclaw-stock 真实链路。
- `codegraph.jsonl` —— `{query, expect_symbol_or_file}`,基于 codegraph 已索引的真实符号。

新增 case 直接往对应 jsonl 追一行即可。

## TODO scaffold(未做)

- **agent 端到端 eval**:给 agent 一个需求 → 看它是否用对 MCP(MCP adoption)、改对文件。
  需 provider(LLM)+ 评判,留作后续。可复用 `codev_platform/agent/loop.py` + `tools/`。
- **retrieval 用 reranker 前后 A/B**:对比纯向量 vs +BM25 +reranker 的 recall 增益。

## 入口约定

从简,**不在 CLI 注册子命令**,直接 `python eval/run_eval.py`(README 即说明)。
