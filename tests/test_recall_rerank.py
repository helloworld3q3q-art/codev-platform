"""recall 精排单测 (recall/rerank.py)。

纯重排(_reorder_by_scores)脱 IO 测; maybe_rerank_hits 测 fail-soft(无模型/模型抛/空)
+ 注入假模型重排。不触 /rerank daemon。
"""
from __future__ import annotations

from codev_platform.recall.rerank import _reorder_by_scores, _rerank_texts, maybe_rerank_hits
from codev_platform.recall.service import CodeRecallHit

PID = "t-recall-rerank"


def _hit(ref, name="n"):
    return CodeRecallHit(ref=ref, score=1.0, name=name, kind="function", file="a.py")


# ---- 纯重排 ----

def test_reorder_by_scores_sorts_desc():
    hits = [_hit("a"), _hit("b"), _hit("c")]
    out = _reorder_by_scores(hits, [0.1, 0.9, 0.5])
    assert [h.ref for h in out] == ["b", "c", "a"]


def test_reorder_stable_on_ties():
    hits = [_hit("a"), _hit("b"), _hit("c")]
    out = _reorder_by_scores(hits, [0.5, 0.5, 0.9])
    assert [h.ref for h in out] == ["c", "a", "b"]   # c 最高, a/b 同分保原序


def test_reorder_len_mismatch_keeps_original():
    hits = [_hit("a"), _hit("b")]
    assert _reorder_by_scores(hits, [0.9]) == hits   # 越界保护 → 原序


def test_reorder_nan_inf_keeps_original():
    # 审计 P2: cross-encoder 对退化文本可能吐 NaN/inf, NaN 比较未定义会乱排 → 必须退原序。
    hits = [_hit("a"), _hit("b"), _hit("c")]
    assert _reorder_by_scores(hits, [0.1, float("nan"), 0.9]) == hits
    assert _reorder_by_scores(hits, [0.1, float("inf"), 0.9]) == hits


# ---- maybe_rerank_hits 编排 ----

def test_no_model_keeps_order(monkeypatch):
    # model=None 且 config 关(build_recall_reranker → None)→ 原序, 零 IO。
    monkeypatch.setattr("codev_platform.recall.rerank.build_recall_reranker", lambda cfg=None: None)
    hits = [_hit("a"), _hit("b")]
    assert maybe_rerank_hits("q", PID, hits) == hits


def test_empty_hits_short_circuit():
    assert maybe_rerank_hits("q", PID, []) == []


class _FakeModel:
    def __init__(self, scores):
        self._scores = scores
    def score(self, query, docs):
        return self._scores[:len(docs)]


def test_injected_model_reorders(monkeypatch):
    # codegraph 取文本会失败(假 pid)→ _rerank_texts fail-soft 退 name; 假模型按分重排。
    hits = [_hit("a"), _hit("b"), _hit("c")]
    out = maybe_rerank_hits("q", PID, hits, model=_FakeModel([0.2, 0.95, 0.4]))
    assert [h.ref for h in out] == ["b", "c", "a"]


def test_model_score_raises_fail_soft():
    class _Boom:
        def score(self, q, docs):
            raise RuntimeError("rerank daemon down")
    hits = [_hit("a"), _hit("b")]
    assert maybe_rerank_hits("q", PID, hits, model=_Boom()) == hits   # 失败 → 原序


def test_rerank_texts_resolves_extra_repo_ref(monkeypatch, tmp_path):
    from codev_platform.core.repos import RepoSpec

    main = tmp_path / "main"
    main.mkdir()
    extra = tmp_path / "extra"
    extra.mkdir()
    specs = [
        RepoSpec(root=main, tag="", is_main=True, source_project_id=PID),
        RepoSpec(root=extra, tag="extra", is_main=False, source_project_id="extra-proj"),
    ]
    seen: list[str] = []

    class FakeCodegraphClient:
        def __init__(self, project_id=None, *, db_path=None):
            self.db_path = db_path

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def node(self, ref):
            seen.append(ref)
            return {
                "id": ref,
                "name": f"name-{ref}",
                "qualifiedName": f"pkg.{ref}",
                "signature": f"def {ref}()",
                "docstring": "",
            }

    monkeypatch.setattr("codev_platform.core.repos.project_repo_specs", lambda pid: specs)
    monkeypatch.setattr("codev_platform.web.integrations.codegraph_client.CodegraphClient",
                        FakeCodegraphClient)

    texts = _rerank_texts(PID, [_hit("main-id"), _hit("extra::extra-id")])

    assert "def main-id()" in texts[0]
    assert "def extra-id()" in texts[1]
    assert seen == ["main-id", "extra-id"]
