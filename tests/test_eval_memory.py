"""eval memory suite 的纯单测 (不碰 PG)。

只覆盖 conflict 子集 (纯逻辑 resolve_conflicts) + accuracy 指标边界。
recall 子集需 PG, 由 scripts/verify_memory_pg.py + run_eval.py --suite memory 真机验证, 这里不测。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from codev_platform.agent.memory_recall import resolve_conflicts
from eval.metrics import accuracy
from eval.suites.memory import _entry_from_dict, _run_memory_conflict

_DATASET = Path(__file__).resolve().parents[1] / "eval" / "datasets" / "memory.jsonl"


def _load_conflict_rows() -> list[dict]:
    rows = []
    with _DATASET.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                d = json.loads(line)
                if d.get("kind") == "conflict":
                    rows.append(d)
    return rows


# ---- accuracy 边界 --------------------------------------------------------

def test_accuracy_basic():
    assert accuracy(3, 4) == 0.75
    assert accuracy(4, 4) == 1.0
    assert accuracy(0, 4) == 0.0


def test_accuracy_zero_total():
    # 无样本约定不算满分
    assert accuracy(0, 0) == 0.0
    assert accuracy(5, 0) == 0.0


# ---- 数据集存在且非空 ------------------------------------------------------

def test_dataset_has_conflict_cases():
    rows = _load_conflict_rows()
    assert len(rows) >= 8, "conflict 子集应有 >=8 条 (覆盖 redline/policy/supersede)"


# ---- conflict 子集逐条断言胜出正确 ----------------------------------------

@pytest.mark.parametrize("row", _load_conflict_rows(),
                         ids=lambda r: r.get("note", "")[:30])
def test_conflict_winner_matches_expect(row):
    # 只把 active 条目喂给 resolve_conflicts (模拟真库 list_scope 只返回 active)
    entries = [_entry_from_dict(e) for e in row["entries"]
               if e.get("status", "active") == "active"]
    resolved = resolve_conflicts(entries, policy=row.get("policy", "personal_first"))
    winner = resolved[0].content if resolved else None
    assert winner == row["expect_winner"], f"{row.get('note')}: got {winner!r}"


# ---- 关键语义点显式覆盖 (不依赖 dataset 顺序) ------------------------------

def test_redline_always_wins_over_nonredline():
    es = [_entry_from_dict({"scope": "personal", "scope_ref": "u", "topic_key": "t",
                            "content": "个人非红线"}),
          _entry_from_dict({"scope": "org", "scope_ref": "org", "topic_key": "t",
                            "content": "org 红线", "is_redline": True})]
    # personal_first 下个人本应优先, 但红线压一切
    win = resolve_conflicts(es, policy="personal_first")
    assert len(win) == 1 and win[0].content == "org 红线"


def test_personal_first_vs_org_first():
    es = [_entry_from_dict({"scope": "org", "scope_ref": "org", "topic_key": "t", "content": "org"}),
          _entry_from_dict({"scope": "personal", "scope_ref": "u", "topic_key": "t", "content": "personal"})]
    assert resolve_conflicts(es, policy="personal_first")[0].content == "personal"
    assert resolve_conflicts(es, policy="org_first")[0].content == "org"


def test_supersede_new_value_wins():
    # 模拟 supersede 留痕: 旧值 status=superseded 不进消解, 新值 active 胜
    es = [_entry_from_dict({"scope": "personal", "scope_ref": "u", "topic_key": "loc",
                            "content": "我搬到上海", "status": "active"})]
    win = resolve_conflicts(es, policy="personal_first")
    assert win[0].content == "我搬到上海"


def test_personal_redline_not_over_org_redline():
    es = [_entry_from_dict({"scope": "personal", "scope_ref": "u", "topic_key": "t",
                            "content": "个人红线", "is_redline": True}),
          _entry_from_dict({"scope": "org", "scope_ref": "org", "topic_key": "t",
                            "content": "org 红线", "is_redline": True})]
    # redline 间按 org 治理序, org 胜 (不受 policy 影响)
    assert resolve_conflicts(es, policy="personal_first")[0].content == "org 红线"


# ---- runner conflict 子集整体跑通 + accuracy 满分 -------------------------

def test_run_memory_conflict_all_correct():
    rows = _load_conflict_rows()
    res = _run_memory_conflict(rows)
    assert res["total"] == len(rows)
    assert res["resolution_accuracy"] == 1.0, \
        f"有错判: {[d for d in res['details'] if not d['ok']]}"
