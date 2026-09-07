"""隔离 executor、configured 输入策略与结果证明测试。"""

from __future__ import annotations

import subprocess
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


def test_runtime_mismatch_fails_before_materialize(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    strategy = support._Strategy(tmp_path)
    runner = support._Runner(0)
    actual = "f" * 64
    support._install_executor(monkeypatch, runner, revision=actual)

    result = executor._execute_attempt(
        support._spec(),
        freeze_attempt_input_strategies({"configured": strategy}),
        cfg={},
    )

    assert strategy.events == []
    assert runner.calls == []
    assert result.outcome is AttemptOutcome.FAILED
    assert result.rc == 1
    assert result.runtime_revision == actual
    assert result.proof.to_value()["success"] is False


@pytest.mark.parametrize(
    ("error", "outcome", "rc", "retryable"),
    [
        (
            AttemptInputError(AttemptOutcome.RETRYABLE, "对象暂不可用", True),
            AttemptOutcome.RETRYABLE,
            2,
            True,
        ),
        (subprocess.TimeoutExpired(["git"], 1.0), AttemptOutcome.TIMED_OUT, 124, True),
        (RuntimeError("未知输入错误"), AttemptOutcome.FAILED, 1, False),
    ],
)
def test_input_failures_map_to_structured_attempt_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    error: BaseException,
    outcome: AttemptOutcome,
    rc: int,
    retryable: bool,
) -> None:
    strategy = support._Strategy(tmp_path, error=error)
    runner = support._Runner(0)
    support._install_executor(monkeypatch, runner)

    result = executor._execute_attempt(
        support._spec(),
        freeze_attempt_input_strategies({"configured": strategy}),
        cfg={},
    )

    assert result.outcome is outcome
    assert result.rc == rc
    assert result.retryable is retryable
    assert result.input_root == ""
    assert result.proof.to_value()["success"] is False
    assert runner.calls == []


@pytest.mark.parametrize(
    ("rc", "input_success", "outcome", "result_rc", "retryable"),
    [
        (2, True, AttemptOutcome.RETRYABLE, 2, True),
        (124, True, AttemptOutcome.FAILED, 124, False),
        (1, True, AttemptOutcome.FAILED, 1, False),
        (-9, True, AttemptOutcome.FAILED, 1, False),
        (300, True, AttemptOutcome.FAILED, 1, False),
        (0, False, AttemptOutcome.FAILED, 1, False),
    ],
)
def test_rc_two_is_retryable_and_rc_zero_requires_success_proof(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    rc: int,
    input_success: bool,
    outcome: AttemptOutcome,
    result_rc: int,
    retryable: bool,
) -> None:
    strategy = support._Strategy(
        tmp_path,
        proof=CanonicalJsonObject.from_value({"success": input_success}),
    )
    runner = support._Runner(rc)
    support._install_executor(monkeypatch, runner)

    result = executor._execute_attempt(
        support._spec(),
        freeze_attempt_input_strategies({"configured": strategy}),
        cfg={},
    )

    assert result.outcome is outcome
    assert result.rc == result_rc
    assert result.retryable is retryable
    assert result.proof.to_value()["success"] is False
    if rc == 0:
        assert strategy.events == ["materialize", "verify"]
    else:
        assert strategy.events == ["materialize"]


def test_executor_redacts_and_limits_failure_note(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret = "raw-secret-value"
    error = RuntimeError(f"token={secret} " + "x" * 5000)
    strategy = support._Strategy(tmp_path, error=error)
    runner = support._Runner(0)
    support._install_executor(monkeypatch, runner)

    result = executor._execute_attempt(
        support._spec(),
        freeze_attempt_input_strategies({"configured": strategy}),
        cfg={},
    )

    assert secret not in result.note
    assert "<redacted>" in result.note
    assert len(result.note) <= 1200


def test_zero_git_oid_fails_before_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    main = tmp_path / "main"
    main.mkdir()
    specs = [RepoSpec(main, "", True, "demo")]
    monkeypatch.setattr("codev_platform.core.repos.project_repo_specs", lambda *_a, **_kw: specs)
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_covers_commit", lambda *_a: True)
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_head", lambda _root: "0" * 40)
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_tree", lambda _root: support._TREE)
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_is_clean", lambda _root: True)

    with pytest.raises(AttemptInputError) as raised:
        ConfiguredAttemptInputStrategy({}).materialize(support._spec())

    assert raised.value.outcome is AttemptOutcome.FAILED
    assert raised.value.retryable is False


def test_configured_strategy_rejects_dirty_worktree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    main = tmp_path / "main"
    main.mkdir()
    specs = [RepoSpec(main, "", True, "demo")]
    monkeypatch.setattr("codev_platform.core.repos.project_repo_specs", lambda *_a, **_kw: specs)
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_covers_commit", lambda *_a: True)
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_is_clean", lambda _root: False)

    with pytest.raises(AttemptInputError) as raised:
        ConfiguredAttemptInputStrategy({}).materialize(support._spec())

    assert raised.value.outcome is AttemptOutcome.FAILED
    assert raised.value.retryable is False
    assert "不干净" in raised.value.note


def test_verify_failure_preserves_input_runner_proof_and_log_ref(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    strategy = support._Strategy(
        tmp_path,
        verify_error=AttemptInputError(AttemptOutcome.RETRYABLE, "输入漂移", True),
    )
    runner = support._Runner(0)
    support._install_executor(monkeypatch, runner)

    result = executor._execute_attempt(
        support._spec(),
        freeze_attempt_input_strategies({"configured": strategy}),
        cfg={},
    )

    proof = result.proof.to_value()
    assert result.outcome is AttemptOutcome.RETRYABLE
    assert result.log_ref == "logs/demo__chroma.log"
    assert proof["success"] is False
    assert proof["input"] == {
        "success": False,
        "root": str(tmp_path),
        "commits": {"main": support._OID},
        "trees": {"main": support._TREE},
    }
    assert proof["runner"] == {"kind": "chroma", "rc": 0, "success": True}
