"""可插拔召回流水线(2026-06-05 解构)。

`recall_service.py` 退化为 back-compat 薄 shim re-export 本包。真身:
- base.py     RecallService / Scorer / Fusion / Reranker 抽象 + RankCtx + 纯 helper(visible_scopes / _score / _rank_for_query)
- scorers.py  KeywordScorer / VectorScorer
- fusion.py   RrfFusion
- reranker.py NoReranker
- service.py  PipelineRecallService(编排 + 4 不变量:ACL / 去重 / redline 置顶 / 截断)
- registry.py register_* + build_recall_service(config 驱动 + 降级,零 if-else)

设计:`memory-recall-pluggable-pipeline-2026-06-05.md`。
"""
