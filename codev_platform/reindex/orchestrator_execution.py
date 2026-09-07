"""单次 process attempt 的两阶段启动、控制循环与死亡收口。"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

from .attempt_artifacts import AttemptArtifactPaths, AttemptArtifactStore
from .attempt_completion import attempt_result_digest
from .attempt_finalization import (
    AttemptFinalizationCheckpoint,
    FinalizationAction,
    FinalizationEvidence,
)
from .attempt_process import (
    AttemptProcessBackend,
    AttemptProcessStartError,
    Deadline,
    ExecutionHandle,
    RecoveryReport,
    RecoveryState,
    validate_death_proof_for_handle,
)
from .attempts import (
    AttemptJournalEntry,
    AttemptOutcome,
    AttemptResult,
    AttemptSpec,
    CanonicalJsonObject,
    ConfirmedProcessDeath,
    ValidatedAttemptResult,
)
from .dependency_gate import DependencyDecision, DependencyDisposition
from .orchestrator_finalizer import AttemptFinalizer
from .orchestrator_models import (
    AttemptJournalPhase,
    AttemptJournalPort,
    AttemptJournalRecord,
    ControlClock,
    OrchestratorClaimLost,
    OrchestratorFatalError,
    OrchestratorSettings,
)
from .orchestrator_process_control import AttemptProcessController
from .queue_ports import ClaimedJob

class AttemptExecutionController:
    """只消费依赖门禁签发的 RUN 决策，并控制一个精确 claim。"""

    def __init__(
        self,
        *,
        process_backend: AttemptProcessBackend,
        artifacts: AttemptArtifactStore,
        journal: AttemptJournalPort,
        finalizer: AttemptFinalizer,
        process_control: AttemptProcessController,
        observer_argv: Callable[[AttemptArtifactPaths], Sequence[str]],
        clock: ControlClock,
        settings: OrchestratorSettings,
        owner_token: str,
        cwd: Path,
    ) -> None:
        self._process = process_backend
        self._artifacts = artifacts
        self._journal = journal
        self._finalizer = finalizer
        self._observer_argv = observer_argv
        self._clock = clock
        self._settings = settings
        self._owner_token = owner_token
        self._control = process_control
        self._cwd = Path(cwd)
        if not self._cwd.is_absolute():
            raise ValueError("attempt cwd 必须是绝对路径")

    def run(
        self,
        claim: ClaimedJob,
        spec: AttemptSpec,
        permit: DependencyDecision,
    ) -> AttemptResult:
        self._validate_run_identity(claim, spec, permit)
        self._control.require_renew(
            claim,
            self._settings.startup_lease_ttl_sec,
            "启动前续租失败",
        )
        paths = self._artifacts.expected(spec.attempt_id)
        claimed = self._entry(claim, spec, paths, AttemptJournalPhase.CLAIMED)
        self._journal.create(AttemptJournalRecord(claimed, spec=spec))
        try:
            initialized = self._artifacts.initialize(spec)
            if initialized != paths:
                raise ValueError("artifact store 返回了非预期路径")
        except MemoryError:
            raise
        except Exception:
            return self._retry_claimed(claim, spec, paths, "artifact 初始化失败")
        preparing = replace(claimed, state=AttemptJournalPhase.PREPARING.value)
        try:
            self._journal.transition(
                AttemptJournalRecord(preparing, spec=spec),
                expected=AttemptJournalPhase.CLAIMED,
            )
        except MemoryError:
            raise
        except Exception:
            return self._retry_claimed(claim, spec, paths, "PREPARING journal 保存失败")
        start_deadline = Deadline.start(
            min(spec.timeout_sec, self._settings.startup_timeout_sec),
            now=self._clock.monotonic(),
        )
        try:
            handle = self._process.prepare(
                attempt_id=spec.attempt_id,
                argv=tuple(self._observer_argv(paths)),
                cwd=self._cwd,
                bootstrap_log=paths.bootstrap_log,
                deadline=start_deadline,
            )
        except AttemptProcessStartError as error:
            return self._handle_start_error(
                claim,
                spec,
                paths,
                preparing,
                error,
                start_deadline,
            )
        except MemoryError:
            raise
        except Exception as error:
            self._handle_unknown_prepare(
                claim,
                spec,
                preparing,
                start_deadline,
                error,
            )
            raise AssertionError("未知 prepare 异常收口后不应返回") from error
        if handle.attempt_id != spec.attempt_id:
            self._control.quarantine(
                claim,
                spec,
                preparing,
                handle,
                AttemptJournalPhase.PREPARING,
                "process backend 返回了其他 attempt 的 handle",
            )
        executing = self._entry(
            claim,
            spec,
            paths,
            AttemptJournalPhase.EXECUTING,
            handle,
        )
        try:
            self._journal.transition(
                AttemptJournalRecord(executing, spec=spec),
                expected=AttemptJournalPhase.PREPARING,
            )
        except Exception:
            death, phase = self._control.terminate_or_quarantine(
                claim,
                spec,
                executing,
                handle,
                AttemptJournalPhase.PREPARING,
            )
            return self._finalize_incomplete(
                claim, spec, paths, executing, phase, death
            )
        if not self._control.renew(claim, self._settings.startup_lease_ttl_sec):
            self._control.terminate_or_quarantine(
                claim,
                spec,
                executing,
                handle,
                AttemptJournalPhase.EXECUTING,
            )
            raise OrchestratorClaimLost("activate 前 lease 已丢失")
        try:
            self._process.activate(handle, start_deadline)
        except AttemptProcessStartError as error:
            if error.handle != handle:
                self._control.quarantine(
                    claim,
                    spec,
                    executing,
                    handle,
                    AttemptJournalPhase.EXECUTING,
                    "activate 失败携带了不匹配 handle",
                )
            death, phase = self._control.death_after_start_error(
                claim,
                spec,
                executing,
                handle,
                AttemptJournalPhase.EXECUTING,
                error,
            )
            if not error.retryable:
                self._control.quarantine(
                    claim,
                    spec,
                    executing,
                    handle,
                    phase,
                    "activate 返回不可重试错误",
                )
            return self._finalize_incomplete(
                claim, spec, paths, executing, phase, death
            )
        except Exception:
            death, phase = self._control.terminate_or_quarantine(
                claim,
                spec,
                executing,
                handle,
                AttemptJournalPhase.EXECUTING,
            )
            return self._finalize_incomplete(
                claim, spec, paths, executing, phase, death
            )
        deadline = Deadline.start(spec.timeout_sec, now=self._clock.monotonic())
        death, interrupted, phase = self._control.control_until_dead(
            claim,
            spec,
            executing,
            handle,
            deadline,
        )
        if interrupted:
            return self._finalize_incomplete(
                claim, spec, paths, executing, phase, death
            )
        try:
            validated = self._artifacts.load_validated(paths, claim)
        except MemoryError:
            raise
        except (OSError, ValueError):
            return self._finalize_incomplete(
                claim, spec, paths, executing, phase, death
            )
        return self._finalize_validated(
            claim,
            spec,
            paths,
            executing,
            phase,
            death,
            validated,
        )

    def _handle_start_error(
        self,
        claim: ClaimedJob,
        spec: AttemptSpec,
        paths: AttemptArtifactPaths,
        preparing: AttemptJournalEntry,
        error: AttemptProcessStartError,
        deadline: Deadline,
    ) -> AttemptResult:
        if error.handle is None:
            report = self._control.recover_preparing(preparing, deadline)
            if report.state is not RecoveryState.NEVER_STARTED:
                return self._finish_recovered_start(
                    claim, spec, paths, preparing, report, error.retryable
                )
            if not error.retryable:
                raise OrchestratorFatalError("prepare 返回不可重试错误，保留 PREPARING")
            return self._finalize_no_process(claim, spec, paths, preparing)
        if error.handle.attempt_id != spec.attempt_id:
            self._control.quarantine(
                claim,
                spec,
                preparing,
                error.handle,
                AttemptJournalPhase.PREPARING,
                "prepare 错误携带其他 attempt handle",
            )
        executing = self._entry(
            claim,
            spec,
            paths,
            AttemptJournalPhase.EXECUTING,
            error.handle,
        )
        phase = AttemptJournalPhase.PREPARING
        try:
            self._journal.transition(
                AttemptJournalRecord(executing, spec=spec),
                expected=AttemptJournalPhase.PREPARING,
            )
            phase = AttemptJournalPhase.EXECUTING
        except Exception:
            pass
        death, phase = self._control.death_after_start_error(
            claim,
            spec,
            executing,
            error.handle,
            phase,
            error,
        )
        if not error.retryable:
            self._control.quarantine(
                claim,
                spec,
                executing,
                error.handle,
                phase,
                "prepare 返回不可重试错误",
            )
        return self._finalize_incomplete(
            claim, spec, paths, executing, phase, death
        )

    def _handle_unknown_prepare(
        self,
        claim: ClaimedJob,
        spec: AttemptSpec,
        preparing: AttemptJournalEntry,
        deadline: Deadline,
        error: Exception,
    ) -> None:
        report = self._control.recover_preparing(preparing, deadline)
        if report.state in {RecoveryState.ACTIVE, RecoveryState.CONFIRMED_DEAD}:
            handle = report.handle
            if handle is None or handle.attempt_id != spec.attempt_id:
                raise OrchestratorFatalError("未知 prepare 异常恢复出错配 handle") from error
            phase = AttemptJournalPhase.PREPARING
            if report.state is RecoveryState.ACTIVE:
                _death, phase = self._control.terminate_or_quarantine(
                    claim,
                    spec,
                    self._entry(
                        claim,
                        spec,
                        self._artifacts.expected(spec.attempt_id),
                        AttemptJournalPhase.EXECUTING,
                        handle,
                    ),
                    handle,
                    phase,
                )
            self._control.quarantine(
                claim,
                spec,
                self._entry(
                    claim,
                    spec,
                    self._artifacts.expected(spec.attempt_id),
                    AttemptJournalPhase.TERMINATING,
                    handle,
                ),
                handle,
                phase,
                "未知 prepare 异常，禁止推断为未启动",
            )
        raise OrchestratorFatalError("未知 prepare 异常无法证明安全，保留 PREPARING") from error

    def _finish_recovered_start(
        self,
        claim: ClaimedJob,
        spec: AttemptSpec,
        paths: AttemptArtifactPaths,
        preparing: AttemptJournalEntry,
        report: RecoveryReport,
        retryable: bool,
    ) -> AttemptResult:
        handle = report.handle
        if handle is None or handle.attempt_id != spec.attempt_id:
            raise OrchestratorFatalError("PREPARING 恢复未返回精确 handle")
        executing = self._entry(
            claim,
            spec,
            paths,
            AttemptJournalPhase.EXECUTING,
            handle,
        )
        phase = AttemptJournalPhase.PREPARING
        if report.state is RecoveryState.ACTIVE:
            death, phase = self._control.terminate_or_quarantine(
                claim, spec, executing, handle, phase
            )
        elif report.state is RecoveryState.CONFIRMED_DEAD:
            if report.death_proof is None:
                raise OrchestratorFatalError("PREPARING 死亡恢复缺少证明")
            death = validate_death_proof_for_handle(handle, report.death_proof)
        else:
            raise OrchestratorFatalError("PREPARING 恢复状态有歧义")
        if not retryable:
            self._control.quarantine(
                claim,
                spec,
                executing,
                handle,
                phase,
                "prepare 返回不可重试错误",
            )
        return self._finalize_incomplete(
            claim, spec, paths, executing, phase, death
        )

    def _retry_claimed(
        self,
        claim: ClaimedJob,
        spec: AttemptSpec,
        paths: AttemptArtifactPaths,
        note: str,
    ) -> AttemptResult:
        self._finalizer.retry_claimed(
            claim=claim,
            spec=spec,
            paths=paths,
            reason=note,
        )
        return self._retry_result(spec, note)

    def _finalize_no_process(
        self,
        claim: ClaimedJob,
        spec: AttemptSpec,
        paths: AttemptArtifactPaths,
        entry: AttemptJournalEntry,
    ) -> AttemptResult:
        checkpoint = AttemptFinalizationCheckpoint(
            1,
            spec.attempt_id,
            spec.fence,
            spec.target_commit,
            FinalizationAction.RETRY,
            FinalizationEvidence.NO_PROCESS_RETRY,
            None,
        )
        self._finalizer.finalize(
            claim=claim,
            spec=spec,
            paths=paths,
            base_entry=entry,
            expected_phase=AttemptJournalPhase.PREPARING,
            checkpoint=checkpoint,
            result=None,
            validated=None,
            death=None,
        )
        return self._retry_result(spec, "prepare 明确未启动")

    def _finalize_incomplete(
        self,
        claim: ClaimedJob,
        spec: AttemptSpec,
        paths: AttemptArtifactPaths,
        entry: AttemptJournalEntry,
        expected_phase: AttemptJournalPhase,
        death: ConfirmedProcessDeath,
    ) -> AttemptResult:
        checkpoint = AttemptFinalizationCheckpoint(
            1,
            spec.attempt_id,
            spec.fence,
            spec.target_commit,
            FinalizationAction.RETRY,
            FinalizationEvidence.INCOMPLETE_ARTIFACT,
            None,
        )
        self._finalizer.finalize(
            claim=claim,
            spec=spec,
            paths=paths,
            base_entry=entry,
            expected_phase=expected_phase,
            checkpoint=checkpoint,
            result=None,
            validated=None,
            death=death,
        )
        return self._retry_result(spec, "attempt 未形成完整完成凭据")

    def _finalize_validated(
        self,
        claim: ClaimedJob,
        spec: AttemptSpec,
        paths: AttemptArtifactPaths,
        entry: AttemptJournalEntry,
        expected_phase: AttemptJournalPhase,
        death: ConfirmedProcessDeath,
        validated: ValidatedAttemptResult,
    ) -> AttemptResult:
        result = validated.result
        action = (
            FinalizationAction.RETRY
            if result.outcome in {AttemptOutcome.RETRYABLE, AttemptOutcome.TIMED_OUT}
            else FinalizationAction.GUARDED_ACK
        )
        checkpoint = AttemptFinalizationCheckpoint(
            1,
            spec.attempt_id,
            spec.fence,
            spec.target_commit,
            action,
            FinalizationEvidence.COMPLETION_RECEIPT,
            attempt_result_digest(result),
        )
        self._finalizer.finalize(
            claim=claim,
            spec=spec,
            paths=paths,
            base_entry=entry,
            expected_phase=expected_phase,
            checkpoint=checkpoint,
            result=result,
            validated=validated,
            death=death,
        )
        return result

    def _entry(
        self,
        claim: ClaimedJob,
        spec: AttemptSpec,
        paths: AttemptArtifactPaths,
        phase: AttemptJournalPhase,
        handle: ExecutionHandle | None = None,
    ) -> AttemptJournalEntry:
        return AttemptJournalEntry(
            1,
            claim.owner_token,
            claim.claim_token,
            spec.attempt_id,
            spec.fence,
            spec.project_id,
            spec.kind,
            str(paths.spec),
            str(paths.result),
            handle.pid if handle else None,
            handle.process_identity if handle else None,
            handle.containment_kind if handle else None,
            handle.native_ref if handle else None,
            phase.value,
            handle.started_at if handle else self._clock.time(),
            spec.timeout_sec,
        )

    @staticmethod
    def _retry_result(spec: AttemptSpec, note: str) -> AttemptResult:
        return AttemptResult(
            1,
            spec.attempt_id,
            spec.fence,
            spec.project_id,
            spec.kind,
            "",
            spec.target_commit,
            (),
            (),
            spec.runtime_revision,
            AttemptOutcome.RETRYABLE,
            None,
            True,
            note,
            (),
            None,
            CanonicalJsonObject.from_value({"success": False}),
        )

    def _validate_run_identity(
        self,
        claim: ClaimedJob,
        spec: AttemptSpec,
        permit: DependencyDecision,
    ) -> None:
        if type(claim) is not ClaimedJob or type(spec) is not AttemptSpec:
            raise ValueError("run 只接受 ClaimedJob 与 AttemptSpec")
        if claim.owner_token != self._owner_token:
            raise OrchestratorFatalError("claim owner 与当前 worker 所有者不匹配")
        if (
            type(permit) is not DependencyDecision
            or permit.disposition is not DependencyDisposition.RUN
        ):
            raise ValueError("执行器只接受依赖门禁签发的 RUN 决策")
        expected = (
            claim.job.project_id,
            claim.job.kind,
            claim.job.meta.target_commit,
        )
        actual = (spec.project_id, spec.kind, spec.target_commit)
        permitted = (permit.project_id, permit.dependent_kind, permit.target_commit)
        if actual != expected or permitted != expected:
            raise ValueError("claim、spec 与依赖 RUN 决策身份不匹配，禁止启动进程")

__all__ = ["AttemptExecutionController"]
