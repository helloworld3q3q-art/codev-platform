"""崩溃后按耐久 journal、queue 与 containment 事实恢复一个 attempt。"""
from __future__ import annotations

from dataclasses import replace

from .attempt_artifacts import AttemptArtifactPaths, AttemptArtifactStore
from .attempt_finalization import (
    AttemptFinalizationCheckpoint,
    FinalizationAction,
    FinalizationEvidence,
)
from .attempt_process import (
    AttemptProcessBackend,
    Deadline,
    ExecutionHandle,
    RecoveryReport,
    RecoveryState,
    TerminationReport,
    validate_death_proof_for_handle,
)
from .attempt_validation import validate_persisted_dependency_block
from .attempts import (
    AttemptJournalEntry,
    AttemptResult,
    AttemptSpec,
    ConfirmedProcessDeath,
    ValidatedAttemptResult,
)
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
from .queue_ports import ClaimedJob, WorkerQueuePort


class AttemptRecoveryController:
    """恢复只重放已持久化意图；不重新运行 observer 或依赖门禁。"""

    def __init__(
        self,
        *,
        queue: WorkerQueuePort,
        process_backend: AttemptProcessBackend,
        process_control: AttemptProcessController,
        artifacts: AttemptArtifactStore,
        journal: AttemptJournalPort,
        finalizer: AttemptFinalizer,
        clock: ControlClock,
        settings: OrchestratorSettings,
    ) -> None:
        self._queue = queue
        self._process = process_backend
        self._control = process_control
        self._artifacts = artifacts
        self._journal = journal
        self._finalizer = finalizer
        self._clock = clock
        self._settings = settings

    def recover(self, recovery_owner_token: str) -> None:
        record = self._load_record()
        owner = record.entry.owner_token if record is not None else recovery_owner_token
        claims = self._recover_owned(owner)
        if record is None:
            self._recover_orphan_claims(claims)
            return
        claim = self._find_exact_claim(record.entry, claims)
        if claim is not None and not self._control.renew(
            claim, self._settings.finalization_lease_ttl_sec
        ):
            claim = None
        self._recover_record(record, claim)

    def _load_record(self) -> AttemptJournalRecord | None:
        try:
            record = self._journal.load()
        except Exception as error:
            raise OrchestratorFatalError("attempt journal 无法读取") from error
        if record is not None and type(record) is not AttemptJournalRecord:
            raise OrchestratorFatalError("attempt journal 类型无效")
        return record

    def _recover_owned(self, owner_token: str) -> list[ClaimedJob]:
        try:
            claims = self._queue.recover_owned(
                owner_token=owner_token,
                timeout_sec=self._settings.queue_op_timeout_sec,
            )
        except Exception as error:
            raise OrchestratorFatalError("queue owned claim 无法读取") from error
        if type(claims) is not list or any(type(item) is not ClaimedJob for item in claims):
            raise OrchestratorFatalError("queue owned claim 返回类型无效")
        if len(claims) > self._settings.max_concurrency:
            raise OrchestratorFatalError("恢复到超出单并发上限的 owned claim")
        return claims

    def _recover_orphan_claims(self, claims: list[ClaimedJob]) -> None:
        for claim in claims:
            if not self._control.renew(claim, self._settings.startup_lease_ttl_sec):
                raise OrchestratorClaimLost("无 journal owned claim 续租失败")
            if not self._queue.retry(
                claim,
                reason="进程启动前崩溃，未形成 attempt journal",
                timeout_sec=self._settings.queue_op_timeout_sec,
            ):
                raise OrchestratorClaimLost("无 journal owned claim retry 未命中")

    def _find_exact_claim(
        self,
        entry: AttemptJournalEntry,
        claims: list[ClaimedJob],
    ) -> ClaimedJob | None:
        if not claims:
            return None
        claim = claims[0]
        expected = (
            entry.owner_token,
            entry.claim_token,
            entry.project_id,
            entry.kind,
        )
        actual = (
            claim.owner_token,
            claim.claim_token,
            claim.job.project_id,
            claim.job.kind,
        )
        if actual != expected:
            raise OrchestratorFatalError("恢复 queue claim 与 journal 身份不匹配")
        return claim

    def _recover_record(
        self,
        record: AttemptJournalRecord,
        claim: ClaimedJob | None,
    ) -> None:
        phase = record.phase
        if phase is AttemptJournalPhase.QUARANTINED:
            raise OrchestratorFatalError("存在 quarantine attempt，禁止自动恢复或领取新任务")
        paths = self._verify_paths(record.entry)
        spec = self._resolve_spec(record, paths)
        if claim is not None:
            self._validate_claim_spec(claim, spec)
        death, expected_phase, entry = self._recover_process(record, claim, spec)
        if claim is None:
            self._finalizer.abandon_without_authority(
                spec=spec,
                paths=paths,
                entry=entry,
                expected_phase=expected_phase,
                checkpoint=record.finalization,
                death=death,
            )
            return
        self._replay_with_authority(
            record,
            claim,
            spec,
            paths,
            death,
            expected_phase,
            entry,
        )

    def _verify_paths(self, entry: AttemptJournalEntry) -> AttemptArtifactPaths:
        try:
            return self._artifacts.verify_journal(entry)
        except Exception as error:
            raise OrchestratorFatalError("attempt artifact 与 journal 无法对账") from error

    def _resolve_spec(
        self,
        record: AttemptJournalRecord,
        paths: AttemptArtifactPaths,
    ) -> AttemptSpec:
        durable = record.spec
        try:
            artifact_spec = self._artifacts.read_spec(paths)
        except Exception:
            artifact_spec = None
        if durable is None and artifact_spec is None:
            raise OrchestratorFatalError("journal 缺少可恢复 AttemptSpec")
        if durable is not None and artifact_spec is not None and durable != artifact_spec:
            raise OrchestratorFatalError("journal 与 artifact AttemptSpec 不一致")
        return durable or artifact_spec  # type: ignore[return-value]

    @staticmethod
    def _validate_claim_spec(claim: ClaimedJob, spec: AttemptSpec) -> None:
        expected = (claim.job.project_id, claim.job.kind, claim.job.meta.target_commit)
        actual = (spec.project_id, spec.kind, spec.target_commit)
        if actual != expected:
            raise OrchestratorFatalError("恢复 claim 与 AttemptSpec 身份不匹配")

    def _recover_process(
        self,
        record: AttemptJournalRecord,
        claim: ClaimedJob | None,
        spec: AttemptSpec,
    ) -> tuple[ConfirmedProcessDeath | None, AttemptJournalPhase, AttemptJournalEntry]:
        phase = record.phase
        if phase is AttemptJournalPhase.CLAIMED:
            return None, phase, record.entry
        if (
            phase is AttemptJournalPhase.FINALIZING
            and record.finalization is not None
            and record.finalization.evidence
            in {
                FinalizationEvidence.DEPENDENCY_BLOCK,
                FinalizationEvidence.NO_PROCESS_RETRY,
            }
        ):
            return None, phase, record.entry
        persisted_handle: ExecutionHandle | None = None
        if phase is AttemptJournalPhase.PREPARING:
            report = self._recover_preparing(record.entry)
        else:
            persisted_handle = self._require_handle(record.entry)
            report = self._recover_handle(persisted_handle)
        if report.state is RecoveryState.NEVER_STARTED:
            if phase is not AttemptJournalPhase.PREPARING:
                raise OrchestratorFatalError("完整 process journal 不得恢复为 NEVER_STARTED")
            return None, phase, record.entry
        if report.state is RecoveryState.UNCONFIRMED:
            if phase is AttemptJournalPhase.PREPARING:
                raise OrchestratorFatalError("PREPARING containment 状态有歧义")
            self._quarantine_ambiguous(
                record,
                claim,
                spec,
                self._require_handle(record.entry),
                phase,
            )
        try:
            handle = self._require_report_handle(report, spec, persisted_handle)
        except OrchestratorFatalError:
            if persisted_handle is not None:
                self._quarantine_ambiguous(
                    record,
                    claim,
                    spec,
                    persisted_handle,
                    phase,
                )
            raise
        entry = self._entry_with_handle(record.entry, handle)
        if report.state is RecoveryState.CONFIRMED_DEAD:
            return self._require_report_proof(handle, report), phase, entry
        if report.state is not RecoveryState.ACTIVE:
            self._quarantine_ambiguous(record, claim, spec, handle, phase)
        if claim is None:
            return self._terminate_unowned(handle), phase, entry
        if phase is AttemptJournalPhase.FINALIZING:
            return self._terminate_finalizing(record, claim, spec, handle), phase, entry
        return self._control.terminate_or_quarantine(
            claim,
            spec,
            entry,
            handle,
            phase,
        ) + (entry,)

    @staticmethod
    def _entry_with_handle(
        entry: AttemptJournalEntry,
        handle: ExecutionHandle,
    ) -> AttemptJournalEntry:
        return replace(
            entry,
            pid=handle.pid,
            process_identity=handle.process_identity,
            containment_kind=handle.containment_kind,
            native_ref=handle.native_ref,
            started_at=handle.started_at,
        )

    def _recover_preparing(self, entry: AttemptJournalEntry) -> RecoveryReport:
        try:
            report = self._process.recover(
                entry,
                self._deadline(),
            )
        except Exception as error:
            raise OrchestratorFatalError("PREPARING containment 恢复失败") from error
        if type(report) is not RecoveryReport:
            raise OrchestratorFatalError("PREPARING 恢复报告类型无效")
        return report

    def _recover_handle(self, handle: ExecutionHandle) -> RecoveryReport:
        try:
            report = self._process.recover_handle(handle, self._deadline())
        except Exception as error:
            raise OrchestratorFatalError("containment handle 恢复失败") from error
        if type(report) is not RecoveryReport:
            raise OrchestratorFatalError("containment 恢复报告类型无效")
        return report

    @staticmethod
    def _require_handle(entry: AttemptJournalEntry) -> ExecutionHandle:
        from .attempt_process import execution_handle_from_journal

        try:
            handle = execution_handle_from_journal(entry)
        except ValueError as error:
            raise OrchestratorFatalError("journal process handle 无效") from error
        if handle is None:
            raise OrchestratorFatalError("该 journal phase 缺少 process handle")
        return handle

    @staticmethod
    def _require_report_handle(
        report: RecoveryReport,
        spec: AttemptSpec,
        persisted_handle: ExecutionHandle | None,
    ) -> ExecutionHandle:
        handle = report.handle
        if handle is None or handle.attempt_id != spec.attempt_id:
            raise OrchestratorFatalError("恢复报告未返回精确 attempt handle")
        if persisted_handle is not None and handle != persisted_handle:
            raise OrchestratorFatalError("恢复报告 handle 与 journal 不匹配")
        return handle

    @staticmethod
    def _require_report_proof(
        handle: ExecutionHandle,
        report: RecoveryReport,
    ) -> ConfirmedProcessDeath:
        if report.death_proof is None:
            raise OrchestratorFatalError("死亡恢复报告缺少证明")
        try:
            return validate_death_proof_for_handle(handle, report.death_proof)
        except ValueError as error:
            raise OrchestratorFatalError("恢复死亡证明与 handle 不匹配") from error

    def _terminate_unowned(self, handle: ExecutionHandle) -> ConfirmedProcessDeath:
        try:
            report = self._process.terminate(
                handle,
                grace_sec=self._settings.kill_grace_sec,
                deadline=self._deadline(),
            )
        except Exception as error:
            raise OrchestratorFatalError("失权 attempt 终止失败，死亡不明") from error
        return self._require_termination_proof(handle, report)

    def _terminate_finalizing(
        self,
        record: AttemptJournalRecord,
        claim: ClaimedJob,
        spec: AttemptSpec,
        handle: ExecutionHandle,
    ) -> ConfirmedProcessDeath:
        try:
            report = self._process.terminate(
                handle,
                grace_sec=self._settings.kill_grace_sec,
                deadline=self._deadline(),
            )
            return self._require_termination_proof(handle, report)
        except Exception as error:
            self._control.quarantine(
                claim,
                spec,
                record.entry,
                handle,
                AttemptJournalPhase.FINALIZING,
                "FINALIZING journal 发现活动 containment",
            )
            raise AssertionError("quarantine 不应返回") from error

    @staticmethod
    def _require_termination_proof(
        handle: ExecutionHandle,
        report: TerminationReport,
    ) -> ConfirmedProcessDeath:
        if type(report) is not TerminationReport or report.death_proof is None:
            raise OrchestratorFatalError("终止后死亡无法确认")
        try:
            return validate_death_proof_for_handle(handle, report.death_proof)
        except ValueError as error:
            raise OrchestratorFatalError("终止死亡证明与 handle 不匹配") from error

    def _quarantine_ambiguous(
        self,
        record: AttemptJournalRecord,
        claim: ClaimedJob | None,
        spec: AttemptSpec,
        handle: ExecutionHandle,
        phase: AttemptJournalPhase,
    ) -> None:
        if claim is None:
            raise OrchestratorFatalError("失权 attempt containment 状态有歧义")
        self._control.quarantine(
            claim,
            spec,
            record.entry,
            handle,
            phase,
            "恢复 containment 状态有歧义",
        )

    def _replay_with_authority(
        self,
        record: AttemptJournalRecord,
        claim: ClaimedJob,
        spec: AttemptSpec,
        paths: AttemptArtifactPaths,
        death: ConfirmedProcessDeath | None,
        expected_phase: AttemptJournalPhase,
        entry: AttemptJournalEntry,
    ) -> None:
        if record.phase is AttemptJournalPhase.CLAIMED:
            self._finalizer.retry_claimed(
                claim=claim,
                spec=spec,
                paths=paths,
                reason="恢复 CLAIMED attempt，未启动 observer",
            )
            return
        if record.phase is AttemptJournalPhase.PREPARING and death is None:
            checkpoint = self._no_process_checkpoint(spec)
            self._finalizer.finalize(
                claim=claim,
                spec=spec,
                paths=paths,
                base_entry=entry,
                expected_phase=expected_phase,
                checkpoint=checkpoint,
                result=None,
                validated=None,
                death=None,
            )
            return
        checkpoint = record.finalization
        if checkpoint is None:
            checkpoint = self._incomplete_checkpoint(spec)
        result, validated = self._recovered_result(checkpoint, spec, paths, claim)
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

    def _recovered_result(
        self,
        checkpoint: AttemptFinalizationCheckpoint,
        spec: AttemptSpec,
        paths: AttemptArtifactPaths,
        claim: ClaimedJob,
    ) -> tuple[AttemptResult | None, ValidatedAttemptResult | None]:
        if checkpoint.evidence in {
            FinalizationEvidence.INCOMPLETE_ARTIFACT,
            FinalizationEvidence.NO_PROCESS_RETRY,
        }:
            return None, None
        if checkpoint.evidence is FinalizationEvidence.COMPLETION_RECEIPT:
            validated = self._artifacts.load_validated(paths, claim)
            return validated.result, validated
        if checkpoint.evidence is FinalizationEvidence.DEPENDENCY_BLOCK:
            result = self._artifacts.read_result(paths)
            if result is None:
                raise OrchestratorFatalError("依赖阻断 FINALIZING 缺少 result")
            validated = validate_persisted_dependency_block(
                spec,
                result,
                claim=claim,
                validated_at=self._clock.time(),
            )
            return result, validated
        raise OrchestratorFatalError("FINALIZING evidence 无效")

    @staticmethod
    def _no_process_checkpoint(spec: AttemptSpec) -> AttemptFinalizationCheckpoint:
        return AttemptFinalizationCheckpoint(
            1,
            spec.attempt_id,
            spec.fence,
            spec.target_commit,
            FinalizationAction.RETRY,
            FinalizationEvidence.NO_PROCESS_RETRY,
            None,
        )

    @staticmethod
    def _incomplete_checkpoint(spec: AttemptSpec) -> AttemptFinalizationCheckpoint:
        return AttemptFinalizationCheckpoint(
            1,
            spec.attempt_id,
            spec.fence,
            spec.target_commit,
            FinalizationAction.RETRY,
            FinalizationEvidence.INCOMPLETE_ARTIFACT,
            None,
        )

    def _deadline(self) -> Deadline:
        return Deadline.start(
            self._settings.recovery_timeout_sec,
            now=self._clock.monotonic(),
        )


__all__ = ["AttemptRecoveryController"]
