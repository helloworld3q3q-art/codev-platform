"""隔离 executor、configured 输入策略与结果证明测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from codev_platform.core.repos import RepoSpec
from codev_platform.reindex import executor
from codev_platform.reindex.attempt_inputs import (
    AttemptInputError,
    ConfiguredAttemptInputStrategy,
    freeze_attempt_input_strategies,
)
from codev_platform.reindex.attempts import (
    AttemptOutcome,
    CanonicalJsonObject,
)

from tests import reindex_executor_support as support


def test_fixed_strategy_mapping_rejects_unknown_or_missing_configured(tmp_path: Path) -> None:
    strategy = support._Strategy(tmp_path)

    with pytest.raises(ValueError):
        freeze_attempt_input_strategies({})
    with pytest.raises(ValueError):
        freeze_attempt_input_strategies({"configured": strategy, "dynamic": strategy})

    source = {"configured": strategy}
    frozen = freeze_attempt_input_strategies(source)
    source.clear()
    assert frozen["configured"] is strategy
    with pytest.raises(TypeError):
        frozen["configured"] = strategy


@pytest.mark.parametrize("repo_key", ["C:/workspace", "/srv/repo", "https://host/repo", "../repo"])
def test_configured_strategy_rejects_payload_remote_or_absolute_path(
    monkeypatch: pytest.MonkeyPatch,
    repo_key: str,
) -> None:
    payload = CanonicalJsonObject.from_value(
        {"project_id": "demo", "repo_targets": {repo_key: support._OID}},
    )
    spec = support._spec(input_payload=payload)
    resolver_called = False

    def _unexpected_resolver(*_args, **_kwargs):
        nonlocal resolver_called
        resolver_called = True
        return []

    monkeypatch.setattr("codev_platform.core.repos.project_repo_specs", _unexpected_resolver)
    with pytest.raises(AttemptInputError) as raised:
        ConfiguredAttemptInputStrategy({}).materialize(spec)

    assert raised.value.outcome is AttemptOutcome.FAILED
    assert raised.value.retryable is False
    assert resolver_called is False


def test_configured_strategy_rejects_path_like_project_id_before_repo_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    malicious = support._spec(
        project_id="../../escape",
        input_payload=CanonicalJsonObject.from_value({"project_id": "../../escape"}),
    )
    resolver_called = False

    def _unexpected_resolver(*_args, **_kwargs):
        nonlocal resolver_called
        resolver_called = True
        return []

    monkeypatch.setattr("codev_platform.core.repos.project_repo_specs", _unexpected_resolver)
    with pytest.raises(AttemptInputError) as raised:
        ConfiguredAttemptInputStrategy({}).materialize(malicious)

    assert raised.value.outcome is AttemptOutcome.FAILED
    assert resolver_called is False


def test_configured_strategy_syncs_only_when_target_is_not_covered(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    main = tmp_path / "main"
    main.mkdir()
    specs = [RepoSpec(root=main, tag="", is_main=True, source_project_id="demo")]
    monkeypatch.setattr("codev_platform.core.repos.project_repo_specs", lambda *_a, **_kw: specs)
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_head", lambda _root: support._OID)
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_tree", lambda _root: support._TREE)
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_is_clean", lambda _root: True)
    sync_calls: list[Path] = []
    covered = iter([False, True])
    monkeypatch.setattr(
        "codev_platform.reindex.git_sync.repo_covers_commit",
        lambda _root, _target: next(covered),
    )
    monkeypatch.setattr(
        "codev_platform.reindex.git_sync.sync_repo_to_remote",
        lambda root: sync_calls.append(root) or {"pulled": True, "note": "ok"},
    )

    materialized = ConfiguredAttemptInputStrategy({}).materialize(support._spec())

    assert materialized == support._materialized(main)
    assert sync_calls == [main]


def test_result_contains_actual_root_commit_and_tree_vectors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    main = tmp_path / "main"
    extra = tmp_path / "extra"
    main.mkdir()
    extra.mkdir()
    specs = [
        RepoSpec(main, "", True, "demo"),
        RepoSpec(extra, "extra", False, "extra-project"),
    ]
    commits = {main: support._OID, extra: support._EXTRA_OID}
    trees = {main: support._TREE, extra: support._EXTRA_TREE}
    monkeypatch.setattr("codev_platform.core.repos.project_repo_specs", lambda *_a, **_kw: specs)
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_covers_commit", lambda *_a: True)
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_head", lambda root: commits[root])
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_tree", lambda root: trees[root])
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_is_clean", lambda _root: True)

    strategy = ConfiguredAttemptInputStrategy({})
    materialized = strategy.materialize(support._spec())
    proof = strategy.verify(support._spec(), materialized).to_value()

    assert materialized.root == str(main)
    assert materialized.input_commits == (("main", support._OID), ("extra", support._EXTRA_OID))
    assert materialized.input_trees == (("main", support._TREE), ("extra", support._EXTRA_TREE))
    assert proof["success"] is True
    assert proof["commits"] == {"extra": support._EXTRA_OID, "main": support._OID}
    assert proof["trees"] == {"extra": support._EXTRA_TREE, "main": support._TREE}


def test_verify_rejects_revision_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    main = tmp_path / "main"
    main.mkdir()
    specs = [RepoSpec(main, "", True, "demo")]
    heads = iter([support._OID, support._EXTRA_OID])
    monkeypatch.setattr("codev_platform.core.repos.project_repo_specs", lambda *_a, **_kw: specs)
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_covers_commit", lambda *_a: True)
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_head", lambda _root: next(heads))
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_tree", lambda _root: support._TREE)
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_is_clean", lambda _root: True)
    strategy = ConfiguredAttemptInputStrategy({})

    materialized = strategy.materialize(support._spec())
    with pytest.raises(AttemptInputError) as raised:
        strategy.verify(support._spec(), materialized)

    assert raised.value.outcome is AttemptOutcome.RETRYABLE
    assert raised.value.retryable is True


def test_verify_rejects_root_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    resolved = iter(
        [
            [RepoSpec(first, "", True, "demo")],
            [RepoSpec(second, "", True, "demo")],
        ]
    )
    monkeypatch.setattr(
        "codev_platform.core.repos.project_repo_specs",
        lambda *_a, **_kw: next(resolved),
    )
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_covers_commit", lambda *_a: True)
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_head", lambda _root: support._OID)
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_tree", lambda _root: support._TREE)
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_is_clean", lambda _root: True)
    strategy = ConfiguredAttemptInputStrategy({})

    materialized = strategy.materialize(support._spec())
    with pytest.raises(AttemptInputError) as raised:
        strategy.verify(support._spec(), materialized)

    assert raised.value.outcome is AttemptOutcome.RETRYABLE
    assert raised.value.retryable is True


def test_materialize_git_runner_and_proof_execute_in_executor_pid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    strategy = support._Strategy(tmp_path, events=events)
    runner = support._Runner(0, events)
    cfg = {"marker": object()}
    support._install_executor(monkeypatch, runner)

    result = executor._execute_attempt(
        support._spec(),
        freeze_attempt_input_strategies({"configured": strategy}),
        cfg=cfg,
    )

    assert events == ["materialize", "runner", "verify"]
    assert strategy.pids == [os.getpid(), os.getpid()]
    assert runner.calls == [("demo", tmp_path, cfg, os.getpid())]
    assert result.outcome is AttemptOutcome.SUCCEEDED
    assert result.input_root == str(tmp_path)
    assert result.input_commits == (("main", support._OID),)
    assert result.input_trees == (("main", support._TREE),)
    assert result.log_ref == "logs/demo__chroma.log"
    assert result.proof.to_value() == {
        "success": True,
        "input": {"kind": "configured", "success": True},
        "runner": {"kind": "chroma", "rc": 0, "success": True},
    }
    timing = dict(result.timing)
    assert timing["finished_at"] >= timing["started_at"] > 0
    assert timing["total"] >= 0
