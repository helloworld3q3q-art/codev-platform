"""attempt 进程轮询、终止、恢复与 quarantine 控制。"""
from __future__ import annotations

from dataclasses import replace

from .attempt_artifacts import AttemptArtifactStore
from .attempt_process import (
    AttemptProcessBackend,
    AttemptProcessStartError,
    Deadline,
    ExecutionHandle,
    RecoveryReport,
    TerminationReport,
    validate_death_proof_for_handle,
)
from .attempts import AttemptJournalEntry, AttemptSpec, ConfirmedProcessDeath
from .orchestrator_models import (
    AttemptJournalPhase,
    AttemptJournalPort,
    AttemptJournalRecord,
    ControlClock,
    ControlStatusPort,
    OrchestratorClaimLost,
    OrchestratorFatalError,
    OrchestratorQuarantined,
    OrchestratorSettings,
)
from .queue_ports import ClaimedJob, QuarantineRecord, WorkerQueuePort


class AttemptProcessController:
    """只管理一个已知 attempt 的 containment 状态，不处理发布。"""

    def __init__(
        self,
        *,
        queue: WorkerQueuePort,
        process_backend: AttemptProcessBackend,
        artifacts: AttemptArtifactStore,
        journal: AttemptJournalPort,
        clock: ControlClock,
        status: ControlStatusPort,
        settings: OrchestratorSettings,
    ) -> None:
        self._queue = queue
        self._process = process_backend
        self._artifacts = artifacts
        self._journal = journal
        self._clock = clock
        self._status = status
        self._settings = settings

    def control_until_dead(
        self,
        claim: ClaimedJob,
        spec: AttemptSpec,
        entry: AttemptJournalEntry,
        handle: ExecutionHandle,
        deadline: Deadline,
    ) -> tuple[ConfirmedProcessDeath, bool, AttemptJournalPhase]:
        next_renew = self._clock.monotonic() + self._settings.renew_sec
        next_heartbeat = self._clock.monotonic()
        while True:
            now = self._clock.monotonic()
            if now >= next_heartbeat:
                try:
                    self._status.heartbeat(AttemptJournalPhase.EXECUTING.value, claim)
                except Exception:
                    death, phase = self.terminate_or_quarantine(
                        claim,
                        spec,
                        entry,
                        handle,
                        AttemptJournalPhase.EXECUTING,
                    )
                    return death, True, phase
                next_heartbeat = now + self._settings.heartbeat_sec
            try:
                process_rc = self._process.poll(handle)
            except Exception:
                death, phase = self.terminate_or_quarantine(
                    claim, spec, entry, handle, AttemptJournalPhase.EXECUTING
                )
                return death, True, phase
            if process_rc is not None:
                proof = self.confirm_exact_death(handle)
                if proof is not None:
                    return proof, False, AttemptJournalPhase.EXECUTING
                death, phase = self.terminate_or_quarantine(
                    claim, spec, entry, handle, AttemptJournalPhase.EXECUTING
                )
                return death, True, phase
            now = self._clock.monotonic()
            if deadline.expired(now=now):
                death, phase = self.terminate_or_quarantine(
                    claim, spec, entry, handle, AttemptJournalPhase.EXECUTING
                )
                return death, True, phase
            if now >= next_renew:
                if not self.renew(claim, self._settings.lease_ttl_sec):
                    self.terminate_or_quarantine(
                        claim, spec, entry, handle, AttemptJournalPhase.EXECUTING
                    )
                    raise OrchestratorClaimLost("执行期间 lease 已丢失")
                next_renew = now + self._settings.renew_sec
            wait = min(
                self._settings.poll_sec,
                deadline.remaining(now=now),
                max(0.0, next_renew - now),
                max(0.0, next_heartbeat - now),
            )
            self._clock.wait(wait)

    def confirm_exact_death(
        self,
        handle: ExecutionHandle,
    ) -> ConfirmedProcessDeath | None:
        try:
            proof = self._process.confirm_dead(
                handle,
                Deadline.start(
                    self._settings.kill_timeout_sec,
                    now=self._clock.monotonic(),
                ),
            )
            if proof is None:
                return None
            return validate_death_proof_for_handle(handle, proof)
        except Exception:
            return None

    def terminate_or_quarantine(
        self,
        claim: ClaimedJob,
        spec: AttemptSpec,
        entry: AttemptJournalEntry,
        handle: ExecutionHandle,
        expected_phase: AttemptJournalPhase,
    ) -> tuple[ConfirmedProcessDeath, AttemptJournalPhase]:
        terminating = replace(
            entry,
            pid=handle.pid,
            process_identity=handle.process_identity,
            containment_kind=handle.containment_kind,
            native_ref=handle.native_ref,
            state=AttemptJournalPhase.TERMINATING.value,
            started_at=handle.started_at,
        )
        current_phase = expected_phase
        try:
            self._journal.transition(
                AttemptJournalRecord(terminating, spec=spec),
                expected=expected_phase,
            )
            current_phase = AttemptJournalPhase.TERMINATING
        except Exception:
            pass
        renewed = self.renew(claim, self._settings.termination_lease_ttl_sec)
        deadline = Deadline.start(
            self._settings.kill_timeout_sec,
            now=self._clock.monotonic(),
        )
        try:
            report = self._process.terminate(
                handle,
                grace_sec=self._settings.kill_grace_sec,
                deadline=deadline,
            )
            death = self._death_from_report(handle, report)
        except Exception:
            self.quarantine(
                claim,
                spec,
                terminating,
                handle,
                current_phase,
                "进程树终止结果有歧义",
            )
        if death is None:
            self.quarantine(
                claim,
                spec,
                terminating,
                handle,
                current_phase,
                "进程树死亡无法确认",
            )
        if not renewed:
            raise OrchestratorClaimLost("终止前续租失败，已杀树并保留 journal")
        return death, current_phase

    def death_after_start_error(
        self,
        claim: ClaimedJob,
        spec: AttemptSpec,
        entry: AttemptJournalEntry,
        handle: ExecutionHandle,
        phase: AttemptJournalPhase,
        error: AttemptProcessStartError,
    ) -> tuple[ConfirmedProcessDeath, AttemptJournalPhase]:
        if error.death_proof is not None:
            try:
                return validate_death_proof_for_handle(handle, error.death_proof), phase
            except ValueError:
                pass
        return self.terminate_or_quarantine(claim, spec, entry, handle, phase)

    def recover_preparing(
        self,
        preparing: AttemptJournalEntry,
        deadline: Deadline,
    ) -> RecoveryReport:
        try:
            report = self._process.recover(preparing, deadline)
        except Exception as error:
            raise OrchestratorFatalError("PREPARING containment 恢复失败") from error
        if type(report) is not RecoveryReport:
            raise OrchestratorFatalError("PREPARING containment 恢复报告无效")
        return report

    def quarantine(
        self,
        claim: ClaimedJob,
        spec: AttemptSpec,
        entry: AttemptJournalEntry,
        handle: ExecutionHandle,
        expected_phase: AttemptJournalPhase,
        reason: str,
    ) -> None:
        queue_error = self._write_queue_quarantine(claim, entry, handle, reason)
        quarantined = replace(
            entry,
            pid=handle.pid,
            process_identity=handle.process_identity,
            containment_kind=handle.containment_kind,
            native_ref=handle.native_ref,
            state=AttemptJournalPhase.QUARANTINED.value,
            started_at=handle.started_at,
        )
        journal_error: Exception | None = None
        try:
            self._journal.transition(
                AttemptJournalRecord(quarantined, spec=spec),
                expected=expected_phase,
            )
        except Exception as error:
            journal_error = error
        cause = queue_error or journal_error
        if cause is None:
            raise OrchestratorQuarantined(reason)
        raise OrchestratorQuarantined(reason) from cause

    def _write_queue_quarantine(
        self,
        claim: ClaimedJob,
        entry: AttemptJournalEntry,
        handle: ExecutionHandle,
        reason: str,
    ) -> Exception | None:
        try:
            record = self._queue.quarantine(
                claim,
                attempt_id=entry.attempt_id,
                fence=entry.fence,
                process_identity=handle.process_identity,
                containment_kind=handle.containment_kind,
                native_ref=handle.native_ref,
                reason=reason,
                timeout_sec=self._settings.queue_op_timeout_sec,
            )
            self._validate_quarantine_record(record, claim, entry, handle, reason)
        except Exception as error:
            return error
        return None

    @staticmethod
    def _validate_quarantine_record(
        record: QuarantineRecord,
        claim: ClaimedJob,
        entry: AttemptJournalEntry,
        handle: ExecutionHandle,
        reason: str,
    ) -> None:
        if type(record) is not QuarantineRecord:
            raise ValueError("queue quarantine 未返回严格记录")
        expected = (
            claim.job.project_id,
            claim.job.kind,
            claim.claim_token,
            entry.attempt_id,
            entry.fence,
            handle.process_identity,
            handle.containment_kind,
            handle.native_ref,
            reason,
        )
        actual = (
            record.project_id,
            record.kind,
            record.claim_token,
            record.attempt_id,
            record.fence,
            record.process_identity,
            record.containment_kind,
            record.native_ref,
            record.reason,
        )
        if actual != expected:
            raise ValueError("queue quarantine 记录与 attempt 不匹配")

    @staticmethod
    def _death_from_report(
        handle: ExecutionHandle,
        report: TerminationReport,
    ) -> ConfirmedProcessDeath | None:
        if type(report) is not TerminationReport or report.death_proof is None:
            return None
        try:
            return validate_death_proof_for_handle(handle, report.death_proof)
        except ValueError:
            return None

    def require_renew(self, claim: ClaimedJob, ttl_sec: float, note: str) -> None:
        if not self.renew(claim, ttl_sec):
            raise OrchestratorClaimLost(note)

    def renew(self, claim: ClaimedJob, ttl_sec: float) -> bool:
        try:
            return bool(
                self._queue.renew(
                    claim,
                    ttl_sec=ttl_sec,
                    timeout_sec=self._settings.queue_op_timeout_sec,
                )
            )
        except Exception:
            return False


__all__ = ["AttemptProcessController"]
