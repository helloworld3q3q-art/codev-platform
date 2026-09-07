"""单次隔离 attempt 的输入物化、runner、复核与原子结果入口。"""
from __future__ import annotations

import argparse
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

import codev_platform.core.runtime_identity as runtime_identity_module
from codev_platform.core.config import load_config
from codev_platform.core.runtime_interpreter import proven_interpreter_path
from codev_platform.reindex import runner_logs
from codev_platform.reindex import runners as _runners
from codev_platform.reindex.attempt_inputs import (
    AttemptInputError,
    AttemptInputStrategy,
    MaterializedInput,
)
from codev_platform.reindex.attempts import (
    AttemptOutcome,
    AttemptResult,
    AttemptSpec,
    CanonicalJsonObject,
    read_attempt_spec,
    write_attempt_result_atomic,
)

_NOTE_LIMIT = 1200
_RETRY_RC = 2
_TIMEOUT_RC = 124
_T = TypeVar("_T")


@dataclass(slots=True)
class _ExecutionState:
    """轻量聚合一次执行的物化事实与阶段耗时。"""

    wall_started_at: float = field(default_factory=time.time)
    monotonic_started_at: float = field(default_factory=time.perf_counter)
    timing: list[tuple[str, float]] = field(default_factory=list)
    materialized: MaterializedInput | None = None
    runner: object | None = None
    runner_rc: int | None = None

    def measure(self, name: str, operation: Callable[[], _T]) -> _T:
        started = time.perf_counter()
        try:
            return operation()
        finally:
            self.timing.append((name, max(0.0, time.perf_counter() - started)))

    def final_timing(self) -> tuple[tuple[str, float], ...]:
        finished_at = time.time()
        total = max(0.0, time.perf_counter() - self.monotonic_started_at)
        return (
            ("started_at", self.wall_started_at),
            *self.timing,
            ("finished_at", max(self.wall_started_at, finished_at)),
            ("total", total),
        )


def _safe_note(value: object) -> str:
    redacted = runner_logs.redact_runner_output(str(value or ""))
    compact = " | ".join(line.strip() for line in redacted.splitlines() if line.strip())
    return compact[:_NOTE_LIMIT]


def _materialized_evidence(state: _ExecutionState) -> dict[str, object] | None:
    value = state.materialized
    if value is None:
        return None
    return {
        "success": False,
        "root": value.root,
        "commits": dict(value.input_commits),
        "trees": dict(value.input_trees),
    }


def _runner_evidence(spec: AttemptSpec, state: _ExecutionState) -> dict[str, object] | None:
    if state.runner is None:
        return None
    return {
        "kind": spec.kind,
        "rc": state.runner_rc,
        "success": state.runner_rc == 0,
    }


def _failure_proof(
    spec: AttemptSpec,
    state: _ExecutionState,
    stage: str,
    *,
    input_evidence: dict[str, object] | None = None,
) -> CanonicalJsonObject:
    value: dict[str, object] = {"success": False, "stage": stage}
    input_value = input_evidence or _materialized_evidence(state)
    runner_value = _runner_evidence(spec, state)
    if input_value is not None:
        value["input"] = input_value
    if runner_value is not None:
        value["runner"] = runner_value
    return CanonicalJsonObject.from_value(value)


def _result(
    spec: AttemptSpec,
    state: _ExecutionState,
    *,
    runtime_revision: str,
    outcome: AttemptOutcome,
    rc: int,
    retryable: bool,
    note: str,
    proof: CanonicalJsonObject,
    log_ref: str | None = None,
) -> AttemptResult:
    materialized = state.materialized
    return AttemptResult(
        schema_version=spec.schema_version,
        attempt_id=spec.attempt_id,
        fence=spec.fence,
        project_id=spec.project_id,
        kind=spec.kind,
        input_root=materialized.root if materialized is not None else "",
        target_commit=spec.target_commit,
        input_commits=materialized.input_commits if materialized is not None else (),
        input_trees=materialized.input_trees if materialized is not None else (),
        runtime_revision=runtime_revision,
        outcome=outcome,
        rc=rc,
        retryable=retryable,
        note=_safe_note(note),
        timing=state.final_timing(),
        log_ref=log_ref or _runner_log_ref(state.runner),
        proof=proof,
    )


def _outcome_rc(outcome: AttemptOutcome) -> int:
    if outcome is AttemptOutcome.RETRYABLE:
        return _RETRY_RC
    if outcome is AttemptOutcome.TIMED_OUT:
        return _TIMEOUT_RC
    return 1


def _typed_failure(
    spec: AttemptSpec,
    state: _ExecutionState,
    runtime_revision: str,
    stage: str,
    error: AttemptInputError,
) -> AttemptResult:
    return _result(
        spec,
        state,
        runtime_revision=runtime_revision,
        outcome=error.outcome,
        rc=_outcome_rc(error.outcome),
        retryable=error.retryable,
        note=error.note,
        proof=_failure_proof(spec, state, stage),
    )


def _unexpected_failure(
    spec: AttemptSpec,
    state: _ExecutionState,
    runtime_revision: str,
    stage: str,
    error: Exception,
) -> AttemptResult:
    return _result(
        spec,
        state,
        runtime_revision=runtime_revision,
        outcome=AttemptOutcome.FAILED,
        rc=1,
        retryable=False,
        note=f"{stage} 意外异常: {type(error).__name__}: {error}",
        proof=_failure_proof(spec, state, stage),
    )


def _timeout_failure(
    spec: AttemptSpec,
    state: _ExecutionState,
    runtime_revision: str,
    stage: str,
) -> AttemptResult:
    return _result(
        spec,
        state,
        runtime_revision=runtime_revision,
        outcome=AttemptOutcome.TIMED_OUT,
        rc=_TIMEOUT_RC,
        retryable=True,
        note=f"{stage} 超时",
        proof=_failure_proof(spec, state, stage),
    )


def _materialize(
    spec: AttemptSpec,
    strategy: AttemptInputStrategy,
    state: _ExecutionState,
    runtime_revision: str,
) -> AttemptResult | None:
    try:
        value = state.measure("materialize", lambda: strategy.materialize(spec))
        if type(value) is not MaterializedInput:
            raise TypeError("materialize 返回类型无效")
        state.materialized = value
    except AttemptInputError as error:
        return _typed_failure(spec, state, runtime_revision, "materialize", error)
    except (subprocess.TimeoutExpired, TimeoutError):
        return _timeout_failure(spec, state, runtime_revision, "materialize")
    except Exception as error:
        return _unexpected_failure(spec, state, runtime_revision, "materialize", error)
    return None


def _runner_log_ref(runner: object) -> str | None:
    value = getattr(runner, "last_log_ref", None)
    return value if type(value) is str and value.strip() else None


def _runner_failure(
    spec: AttemptSpec,
    state: _ExecutionState,
    runtime_revision: str,
    runner: object,
    rc: int,
) -> AttemptResult:
    if rc == _RETRY_RC:
        outcome, retryable = AttemptOutcome.RETRYABLE, True
    elif rc == _TIMEOUT_RC:
        # runner 已有内部受控时限；无进展超时必须形成失败终态，避免队列无限重放。
        # code_vec 的任意非零退出若本次 checkpoint 有推进，会由 runner 映射为 rc=2；
        # 零进展仍保留原返回码形成终态，避免无限重放。
        outcome, retryable = AttemptOutcome.FAILED, False
    else:
        outcome, retryable = AttemptOutcome.FAILED, False
    process_rc = rc if 0 < rc < 256 else 1
    note = getattr(runner, "last_note", "") or f"runner 返回非零码: {rc}"
    return _result(
        spec,
        state,
        runtime_revision=runtime_revision,
        outcome=outcome,
        rc=process_rc,
        retryable=retryable,
        note=str(note),
        proof=_failure_proof(spec, state, "runner"),
        log_ref=_runner_log_ref(runner),
    )


def _run_registered_runner(
    spec: AttemptSpec,
    state: _ExecutionState,
    runtime_revision: str,
    interpreter: Path,
    cfg: dict,
) -> tuple[object, int] | AttemptResult:
    try:
        _runners.bind_attempt_context(
            cfg,
            spec.attempt_id,
            runtime_revision=runtime_revision,
            interpreter=interpreter,
            target_commit=spec.target_commit,
        )
    except ValueError as error:
        return _unexpected_failure(spec, state, runtime_revision, "runner_context", error)
    runner = _runners.get_runner(spec.kind)
    if runner is None:
        return _result(
            spec,
            state,
            runtime_revision=runtime_revision,
            outcome=AttemptOutcome.FAILED,
            rc=1,
            retryable=False,
            note=f"未注册 runner: {spec.kind}",
            proof=_failure_proof(spec, state, "runner_lookup"),
        )
    state.runner = runner
    try:
        root = Path(state.materialized.root)  # type: ignore[union-attr]
        rc = state.measure("runner", lambda: runner.run(spec.project_id, root, cfg))
        if type(rc) is not int:
            raise TypeError("runner 返回码类型无效")
        state.runner_rc = rc
    except (subprocess.TimeoutExpired, TimeoutError):
        return _timeout_failure(spec, state, runtime_revision, "runner")
    except Exception as error:
        return _unexpected_failure(spec, state, runtime_revision, "runner", error)
    if rc != 0:
        return _runner_failure(spec, state, runtime_revision, runner, rc)
    return runner, rc


def _verify_success(
    spec: AttemptSpec,
    strategy: AttemptInputStrategy,
    state: _ExecutionState,
    runtime_revision: str,
    runner: object,
) -> AttemptResult:
    try:
        materialized = state.materialized
        proof = state.measure("verify", lambda: strategy.verify(spec, materialized))  # type: ignore[arg-type]
        if type(proof) is not CanonicalJsonObject:
            raise TypeError("verify 返回类型无效")
    except AttemptInputError as error:
        return _typed_failure(spec, state, runtime_revision, "verify", error)
    except (subprocess.TimeoutExpired, TimeoutError):
        return _timeout_failure(spec, state, runtime_revision, "verify")
    except Exception as error:
        return _unexpected_failure(spec, state, runtime_revision, "verify", error)

    input_proof = proof.to_value()
    runner_proof = {"kind": spec.kind, "rc": 0, "success": True}
    if input_proof.get("success") is not True:
        return _result(
            spec,
            state,
            runtime_revision=runtime_revision,
            outcome=AttemptOutcome.FAILED,
            rc=1,
            retryable=False,
            note="input proof 未声明成功",
            proof=_failure_proof(
                spec,
                state,
                "verify",
                input_evidence=input_proof,
            ),
            log_ref=_runner_log_ref(runner),
        )
    return _result(
        spec,
        state,
        runtime_revision=runtime_revision,
        outcome=AttemptOutcome.SUCCEEDED,
        rc=0,
        retryable=False,
        note="",
        proof=CanonicalJsonObject.from_value({
            "success": True,
            "input": input_proof,
            "runner": runner_proof,
        }),
        log_ref=_runner_log_ref(runner),
    )


def _execute_attempt(
    spec: AttemptSpec,
    strategies: Mapping[str, AttemptInputStrategy],
    *,
    cfg: dict,
) -> AttemptResult:
    state = _ExecutionState()
    identity = state.measure("runtime_identity", runtime_identity_module.runtime_identity)
    runtime_revision = identity.runtime_revision
    if runtime_revision != spec.runtime_revision:
        return _result(
            spec,
            state,
            runtime_revision=runtime_revision,
            outcome=AttemptOutcome.FAILED,
            rc=1,
            retryable=False,
            note="executor runtime revision 与 spec 不一致",
            proof=_failure_proof(spec, state, "runtime_identity"),
        )
    try:
        interpreter = state.measure(
            "runtime_interpreter",
            lambda: proven_interpreter_path(identity),
        )
    except Exception as error:
        return _unexpected_failure(
            spec,
            state,
            runtime_revision,
            "runtime_interpreter",
            error,
        )
    strategy = strategies.get(spec.input_kind)
    if strategy is None:
        return _result(
            spec,
            state,
            runtime_revision=runtime_revision,
            outcome=AttemptOutcome.FAILED,
            rc=1,
            retryable=False,
            note=f"未注册 input strategy: {spec.input_kind}",
            proof=_failure_proof(spec, state, "strategy_lookup"),
        )
    failed = _materialize(spec, strategy, state, runtime_revision)
    if failed is not None:
        return failed
    runner_result = _run_registered_runner(
        spec,
        state,
        runtime_revision,
        interpreter,
        cfg,
    )
    if isinstance(runner_result, AttemptResult):
        return runner_result
    runner, _rc = runner_result
    return _verify_success(spec, strategy, state, runtime_revision, runner)


def execute_attempt(
    spec: AttemptSpec,
    strategies: Mapping[str, AttemptInputStrategy],
) -> AttemptResult:
    """固定二参契约；生产 CLI 使用显式同一 cfg 的私有执行核心。"""
    return _execute_attempt(spec, strategies, cfg=load_config())


def _bootstrap_strategies(cfg: dict) -> Mapping[str, AttemptInputStrategy]:
    """子进程最小组合边界，只构造代码内固定白名单策略。"""
    from codev_platform.reindex.executor_bootstrap import build_attempt_input_strategies
    return build_attempt_input_strategies(cfg)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="执行单次隔离 reindex attempt")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--result", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        spec = read_attempt_spec(Path(args.spec))
        cfg = load_config()
        strategies = _bootstrap_strategies(cfg)
        result = _execute_attempt(spec, strategies, cfg=cfg)
        write_attempt_result_atomic(Path(args.result), result)
    except Exception:
        return 1
    return result.rc if result.rc is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
