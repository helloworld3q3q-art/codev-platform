"""eval/metrics.py 纯单测 (无 IO, 无后端)。

覆盖 recall_at_k / hit_at_k / mrr / precision_at_k 的正常 + 边界 (空 / 全中 / 全不中 / k 截断)。
"""
from __future__ import annotations

from eval.metrics import (
    aggregate_mrr,
    hit_at_k,
    mrr,
    precision_at_k,
    recall_at_k,
)


# ---- recall_at_k ----------------------------------------------------------

def test_recall_at_k_partial():
    # 相关 {a,b,c}, 前 3 命中 a,c -> 2/3
    assert recall_at_k(["a", "x", "c", "b"], {"a", "b", "c"}, 3) == 2 / 3


def test_recall_at_k_all_hit_and_k_truncation():
    # 全中但 k 截断: 前 2 只能命中 2/3
    assert recall_at_k(["a", "b", "c"], {"a", "b", "c"}, 2) == 2 / 3
    # k 足够 -> 满分
    assert recall_at_k(["a", "b", "c"], {"a", "b", "c"}, 3) == 1.0


def test_recall_at_k_empty_relevant_and_no_hit():
    assert recall_at_k(["a", "b"], set(), 5) == 0.0          # 空相关集 -> 0
    assert recall_at_k(["x", "y"], {"a", "b"}, 5) == 0.0     # 全不中 -> 0


# ---- hit_at_k -------------------------------------------------------------

def test_hit_at_k_true_within_k():
    assert hit_at_k(["x", "a", "y"], {"a"}, 2) is True       # a 在 rank1 (index1) <k


def test_hit_at_k_false_when_relevant_beyond_k():
    assert hit_at_k(["x", "y", "a"], {"a"}, 2) is False      # a 在 index2, 被 k=2 截掉


def test_hit_at_k_empty_and_no_hit():
    assert hit_at_k(["a"], set(), 3) is False
    assert hit_at_k(["x", "y"], {"a"}, 3) is False


# ---- mrr ------------------------------------------------------------------

def test_mrr_first_hit_rank():
    assert mrr(["x", "a", "b"], {"a", "b"}) == 0.5           # 首命中 rank2 -> 1/2
    assert mrr(["a", "x"], {"a"}) == 1.0                     # rank1 -> 1


def test_mrr_no_hit_and_empty():
    assert mrr(["x", "y"], {"a"}) == 0.0
    assert mrr(["a"], set()) == 0.0


def test_aggregate_mrr_average():
    # query1 首命中 rank1 (1.0), query2 首命中 rank2 (0.5) -> 平均 0.75
    pq = [(["a", "x"], {"a"}), (["x", "b"], {"b"})]
    assert aggregate_mrr(pq) == 0.75
    assert aggregate_mrr([]) == 0.0


# ---- precision_at_k -------------------------------------------------------

def test_precision_at_k_partial():
    # 前 4 命中 a,c -> 2/4 = 0.5
    assert precision_at_k(["a", "x", "c", "y"], {"a", "c"}, 4) == 0.5


def test_precision_at_k_full_and_truncation():
    assert precision_at_k(["a", "b"], {"a", "b"}, 2) == 1.0
    # 分母固定 k: 召回不足 k 个也按 k 算
    assert precision_at_k(["a"], {"a"}, 5) == 1 / 5


def test_precision_at_k_zero_k_and_no_hit():
    assert precision_at_k(["a"], {"a"}, 0) == 0.0
    assert precision_at_k(["x", "y"], {"a"}, 2) == 0.0
