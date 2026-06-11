"""recall 精排 (Phase 6 reranker) —— 对融合后的 top 候选用 cross-encoder 重排。

加权 RRF 是**召回**(粗排, 保证相关项进 top-N); reranker 是**精排**(cross-encoder 读
query+候选全文逐对打分, 把最相关顶到最前)。复用平台 Qwen3-Reranker(走 chroma daemon
`/rerank`, 与 agent memory 精排同源), 不另起模型。

铁律(对齐 plan Phase 6 Gate「reranker 关闭仍稳定」+ agent-provider-architecture fail-soft):
- **config 门控** `recall.rerank.enabled`(默认关)→ 关 / 模型不可用 / 任何异常 → **原序返回**
  (即加权 RRF 序), 绝不因精排失败丢结果。
- **精排文本走 codegraph node()**(name+qualifiedName+signature+docstring, 与 vector 嵌入同源);
  非 codegraph ref(如 graph db_table)退回 name —— 单次只读 sqlite 点查, 不起 daemon。
- `_reorder_by_scores` 纯函数(脱 IO 可单测); IO(取文本 / 调 /rerank)是薄壳。
"""
from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)


def _reorder_by_scores(hits: list, scores: list[float]) -> list:
    """按 rerank 分降序重排 hits(稳定: 同分保原序)。len 不匹配 / 含 NaN·inf → 原序(防越界 + 防
    NaN 比较未定义把项错插中间)。纯函数。"""
    if not scores or len(scores) != len(hits):
        return hits
    if not all(isinstance(s, (int, float)) and math.isfinite(s) for s in scores):
        return hits   # NaN/inf(cross-encoder 对退化文本可能吐)→ fail-soft 退原序, 不乱排
    order = sorted(range(len(hits)), key=lambda i: -scores[i])   # sorted 稳定 → 同分保原序
    return [hits[i] for i in order]


def _rerank_texts(project_id: str, hits: list) -> list[str]:
    """每个候选的精排文本: codegraph node 的 name+sig+docstring(与 vector 嵌入同源),
    非 codegraph ref 退回 name。一次只读连接批量点查; 失败 → 全退 name(fail-soft)。"""
    from codev_platform.recall.code_vector_store import build_text

    fallback = [(h.name or h.ref) for h in hits]
    try:
        from codev_platform.web.integrations.codegraph_client import CodegraphClient
        with CodegraphClient(project_id) as cg:
            out: list[str] = []
            for i, h in enumerate(hits):
                try:
                    node = cg.node(h.ref)
                except Exception:  # noqa: BLE001 — 单点查失败不拖垮整批
                    node = None
                text = build_text(node) if node else ""
                out.append(text.strip() or fallback[i])
            return out
    except Exception as exc:  # noqa: BLE001 — codegraph db 缺等 → 全退 name
        logger.warning("[recall.rerank] 取精排文本失败, 退回 name: %r", exc)
        return fallback


def build_recall_reranker(cfg: dict | None = None):
    """config 门控建 reranker; 关闭 / 不可用 → None(调用侧据此跳过精排, 保加权 RRF 序)。"""
    from codev_platform.core.config import get as _get
    from codev_platform.core.config import load_config
    cfg = cfg if cfg is not None else load_config()
    if not _get(cfg, "recall.rerank.enabled", False):
        return None
    from codev_platform.agent.embed.registry import build_rerank_model
    return build_rerank_model(cfg)


def maybe_rerank_hits(query: str, project_id: str, hits: list, *,
                      model=None, cfg: dict | None = None) -> list:
    """对 hits 精排(若 config 开且模型可用); 否则 / 失败 → 原序(加权 RRF)。

    model: 显式注入(测试 / 复用); None → 按 config 建(默认关 → 不精排, 零 IO)。
    """
    if not hits:
        return hits
    rr = model if model is not None else build_recall_reranker(cfg)
    if rr is None:
        return hits   # 关闭 / 不可用 → 加权 RRF 序(plan Gate: reranker 关仍稳定)
    try:
        texts = _rerank_texts(project_id, hits)
        scores = rr.score(query, texts)
        return _reorder_by_scores(hits, scores)
    except Exception as exc:  # noqa: BLE001 — 精排失败绝不丢结果
        logger.warning("[recall.rerank] 精排失败, 退回加权 RRF 序: %r", exc)
        return hits
