"""死亡证明之后的 attempt 最终化顺序与 queue 发布动作。"""
from __future__ import annotations

from dataclasses import replace

from .attempt_artifacts import AttemptArtifactPaths, AttemptArtifactStore
from .attempt_cleanup import AttemptCleanupPort
from .attempt_finalization import (
    AttemptFinalizationCheckpoint,
    FinalizationAction,
    FinalizationEvidence,
    validate_finalization_checkpoint,
)
from .attempt_process import (
    Deadline,
    execution_handle_from_journal,
    validate_death_proof_for_handle,
)
from .attempts import (
    AttemptJournalEntry,
    AttemptResult,
    AttemptSpec,
    ConfirmedProcessDeath,
    ValidatedAttemptResult,
)
from .orchestrator_models import (
    AttemptJournalPhase,
    AttemptJournalPort,
    AttemptJournalRecord,
    ControlClock,
    HealthRefreshPort,
    OrchestratorClaimLost,
    OrchestratorFatalError,
    OrchestratorSettings,
)
from .queue_ports import ClaimedJob, WorkerQueuePort
from .result_publisher import ResultPublisher


class AttemptFinalizer:
    """只处理已经确定动作的清理、queue 动作与耐久收口。"""

    def __init__(
        self,
        *,
        queue: WorkerQueuePort,
        publisher: ResultPublisher,
        journal: AttemptJournalPort,
        cleanup_router: AttemptCleanupPort,
        artifacts: AttemptArtifactStore,
        health_refresh: HealthRefreshPort,
        clock: ControlClock,
        settings: OrchestratorSettings,
    ) -> None:
        self._queue = queue
        self._publisher = publisher
        self._journal = journal
        self._cleanup = cleanup_router
        self._artifacts = artifacts
        self._health = health_refresh
        self._clock = clock
        self._settings = settings

    def finalize(
        self,
        *,
        claim: ClaimedJob,
        spec: AttemptSpec,
        paths: AttemptArtifactPaths,
        base_entry: AttemptJournalEntry,
        expected_phase: AttemptJournalPhase,
        checkpoint: AttemptFinalizationCheckpoint,
        result: AttemptResult | None,
        validated: ValidatedAttemptResult | None,
        death: ConfirmedProcessDeath | None,
    ) -> AttemptResult | None:
        final_entry = replace(base_entry, state=AttemptJournalPhase.FINALIZING.value)
        validate_finalization_checkpoint(
            checkpoint,
            final_entry,
            spec=spec,
            result=result,
        )
        self._journal.transition(
            AttemptJournalRecord(final_entry, checkpoint, spec),
            expected=expected_phase,
        )
        self._renew_cleanup_window(claim)
        self._release_input(spec, base_entry, checkpoint, death)
        self._renew_cleanup_window(claim)
        terminal = self._apply_queue_action(
            claim=claim,
            spec=spec,
            checkpoint=checkpoint,
            validated=validated,
        )
        self._artifacts.cleanup(paths)
        self._journal.clear(
            attempt_id=spec.attempt_id,
            fence=spec.fence,
            expected=AttemptJournalPhase.FINALIZING,
        )
        if terminal:
            self._refresh_terminal_health(spec.project_id)
        return result

    def retry_claimed(
        self,
        *,
        claim: ClaimedJob,
        spec: AttemptSpec,
        paths: AttemptArtifactPaths,
        reason: str,
    ) -> None:
        """收口 artifact 初始化前后的明确无进程失败。"""
        deadline = Deadline.start(
            self._settings.cleanup_timeout_sec,
            now=self._clock.monotonic(),
        )
        report = self._cleanup.release_unstarted(spec, deadline)
        if not report.released or report.attempt_id != spec.attempt_id:
            raise OrchestratorFatalError("未启动输入清理失败，保留 CLAIMED journal")
        if not self._queue.retry(
            claim,
            reason=reason,
            timeout_sec=self._settings.queue_op_timeout_sec,
        ):
            raise OrchestratorClaimLost("无进程 retry 未命中精确 claim")
        self._artifacts.cleanup(paths)
        self._journal.clear(
            attempt_id=spec.attempt_id,
            fence=spec.fence,
            expected=AttemptJournalPhase.CLAIMED,
        )

    def abandon_without_authority(
        self,
        *,
        spec: AttemptSpec,
        paths: AttemptArtifactPaths,
        entry: AttemptJournalEntry,
        expected_phase: AttemptJournalPhase,
        checkpoint: AttemptFinalizationCheckpoint | None,
        death: ConfirmedProcessDeath | None,
    ) -> None:
        """旧 claim 已失权后只回收本 attempt 私有资源，绝不改 queue。"""
        cleanup_checkpoint = checkpoint or AttemptFinalizationCheckpoint(
            1,
            spec.attempt_id,
            spec.fence,
            spec.target_commit,
            FinalizationAction.RETRY,
            (
                FinalizationEvidence.NO_PROCESS_RETRY
                if entry.pid is None
                else FinalizationEvidence.INCOMPLETE_ARTIFACT
            ),
            None,
        )
        self._release_input(spec, entry, cleanup_checkpoint, death)
        self._artifacts.cleanup(paths)
        terminal = checkpoint is not None and checkpoint.action is FinalizationAction.GUARDED_ACK
        self._journal.clear(
            attempt_id=spec.attempt_id,
            fence=spec.fence,
            expected=expected_phase,
        )
        if terminal:
            self._refresh_terminal_health(spec.project_id)

    def _renew_cleanup_window(self, claim: ClaimedJob) -> None:
        renewed = self._queue.renew(
            claim,
            ttl_sec=self._settings.finalization_lease_ttl_sec,
            timeout_sec=self._settings.queue_op_timeout_sec,
        )
        if not renewed:
            raise OrchestratorClaimLost("最终化前续租失败，保留 FINALIZING journal")

    def _refresh_terminal_health(self, project_id: str) -> None:
        """业务终态完成后刷新独立 health 状态；失败不得恢复 attempt journal。"""
        self._health.request(project_id)
        report = self._health.flush(
            Deadline.start(
                self._settings.health_timeout_sec,
                now=self._clock.monotonic(),
            )
        )
        if getattr(report, "containment_confirmed_dead", True) is not True:
            raise OrchestratorFatalError("业务终态已提交，但 health containment 死亡未证明")
        attempted = getattr(report, "attempted_projects", ())
        if project_id not in attempted:
            raise OrchestratorFatalError(
                "业务终态已提交，但 health 刷新未尝试目标项目"
            )

    def _release_input(
        self,
        spec: AttemptSpec,
        base_entry: AttemptJournalEntry,
        checkpoint: AttemptFinalizationCheckpoint,
        death: ConfirmedProcessDeath | None,
    ) -> None:
        deadline = Deadline.start(
            self._settings.cleanup_timeout_sec,
            now=self._clock.monotonic(),
        )
        if checkpoint.evidence in {
            FinalizationEvidence.DEPENDENCY_BLOCK,
            FinalizationEvidence.NO_PROCESS_RETRY,
        }:
            report = self._cleanup.release_unstarted(spec, deadline)
        else:
            if type(death) is not ConfirmedProcessDeath:
                raise OrchestratorFatalError("缺少匹配死亡证明，禁止清理 attempt 输入")
            handle = execution_handle_from_journal(base_entry)
            if handle is None:
                raise OrchestratorFatalError("进程最终化缺少完整 handle")
            try:
                validate_death_proof_for_handle(handle, death)
            except ValueError as error:
                raise OrchestratorFatalError("死亡证明与 attempt handle 不匹配") from error
            report = self._cleanup.release(spec, death, deadline)
        if not report.released or report.attempt_id != spec.attempt_id:
            raise OrchestratorFatalError("attempt 输入清理未完成，保留 FINALIZING journal")

    def _apply_queue_action(
        self,
        *,
        claim: ClaimedJob,
        spec: AttemptSpec,
        checkpoint: AttemptFinalizationCheckpoint,
        validated: ValidatedAttemptResult | None,
    ) -> bool:
        if checkpoint.action is FinalizationAction.RETRY:
            retried = self._queue.retry(
                claim,
                reason="attempt 需要安全重试",
                timeout_sec=self._settings.queue_op_timeout_sec,
            )
            if not retried:
                raise OrchestratorClaimLost("retry 未命中精确 claim")
            return False
        return self._guarded_ack(claim, spec, checkpoint, validated)

    def _guarded_ack(
        self,
        claim: ClaimedJob,
        spec: AttemptSpec,
        checkpoint: AttemptFinalizationCheckpoint,
        validated: ValidatedAttemptResult | None,
    ) -> bool:
        with self._queue.begin_publish(
            claim,
            desired_revision=spec.target_commit,
            timeout_sec=self._settings.queue_op_timeout_sec,
        ) as permit:
            if not permit.superseded:
                if validated is None:
                    raise OrchestratorFatalError("发布分支缺少已验证结果")
                receipt = self._publisher.publish(validated)
                if (
                    not receipt.published
                    or receipt.attempt_id != spec.attempt_id
                    or receipt.result_digest != checkpoint.result_digest
                ):
                    raise OrchestratorFatalError("manifest 未形成精确 attempt 发布见证")
            if not permit.ack():
                raise OrchestratorClaimLost("发布围栏 ack 未命中精确 claim")
        return True


__all__ = ["AttemptFinalizer"]
