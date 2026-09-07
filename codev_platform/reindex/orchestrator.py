"""reindex queue 门禁与单次执行控制器的唯一轻量聚合。"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from .attempt_artifacts import AttemptArtifactPaths, AttemptArtifactStore
from .attempt_cleanup import AttemptCleanupPort
from .attempt_completion import attempt_result_digest
from .attempt_finalization import (
    AttemptFinalizationCheckpoint,
    FinalizationAction,
    FinalizationEvidence,
)
from .attempt_process import AttemptProcessBackend, Deadline
from .attempts import AttemptJournalEntry, AttemptResult, AttemptSpec
from .dependency_gate import (
    DependencyDisposition,
    DependencyGate,
    validate_dependency_block,
)
from .orchestrator_execution import AttemptExecutionController
from .orchestrator_finalizer import AttemptFinalizer
from .orchestrator_models import (
    AttemptJournalPhase,
    AttemptJournalPort,
    AttemptJournalRecord,
    ControlClock,
    ControlStatusPort,
    HealthRefreshPort,
    OrchestratorClaimLost,
    OrchestratorFatalError,
    OrchestratorQuarantined,
    OrchestratorSettings,
    NullControlStatus,
)
from .orchestrator_process_control import AttemptProcessController
from .orchestrator_recovery import AttemptRecoveryController
from .queue_ports import ClaimedJob, WorkerQueuePort, validate_target_commit
from .result_publisher import ResultPublisher
from .spec_factory import AttemptSpecFactory


class AttemptOrchestrator:
    """固定 max_concurrency=1；自身不运行 Git、runner 或输入物化。"""

    def __init__(
        self,
        *,
        queue: WorkerQueuePort,
        process_backend: AttemptProcessBackend,
        artifacts: AttemptArtifactStore,
        dependency_gate: DependencyGate,
        publisher: ResultPublisher,
        journal: AttemptJournalPort,
        cleanup_router: AttemptCleanupPort,
        spec_factory: AttemptSpecFactory,
        observer_argv: Callable[[AttemptArtifactPaths], Sequence[str]],
        health_refresh: HealthRefreshPort,
        clock: ControlClock,
        settings: OrchestratorSettings,
        owner_token: str,
        projects: set[str] | None,
        cwd: Path,
        status: ControlStatusPort | None = None,
        recovery_owner_token: str | None = None,
    ) -> None:
        if type(owner_token) is not str or not owner_token.strip():
            raise ValueError("owner_token 必须是非空字符串")
        self._queue = queue
        self._artifacts = artifacts
        self._gate = dependency_gate
        self._journal = journal
        self._factory = spec_factory
        self._health = health_refresh
        self._settings = settings
        self._status = status or NullControlStatus()
        self._owner_token = owner_token
        if recovery_owner_token is not None and (
            type(recovery_owner_token) is not str or not recovery_owner_token.strip()
        ):
            raise ValueError("recovery_owner_token 必须是非空字符串或 None")
        self._recovery_owner_token = recovery_owner_token or owner_token
        self._projects = None if projects is None else frozenset(projects)
        finalizer = AttemptFinalizer(
            queue=queue,
            publisher=publisher,
            journal=journal,
            cleanup_router=cleanup_router,
            artifacts=artifacts,
            health_refresh=health_refresh,
            clock=clock,
            settings=settings,
        )
        process_control = AttemptProcessController(
            queue=queue,
            process_backend=process_backend,
            artifacts=artifacts,
            journal=journal,
            clock=clock,
            status=self._status,
            settings=settings,
        )
        self._execution = AttemptExecutionController(
            process_backend=process_backend,
            artifacts=artifacts,
            journal=journal,
            finalizer=finalizer,
            process_control=process_control,
            observer_argv=observer_argv,
            clock=clock,
            settings=settings,
            owner_token=owner_token,
            cwd=Path(cwd),
        )
        self._clock = clock
        self._finalizer = finalizer
        self._process = process_backend
        self._recovery = AttemptRecoveryController(
            queue=queue,
            process_backend=process_backend,
            process_control=process_control,
            artifacts=artifacts,
            journal=journal,
            finalizer=finalizer,
            clock=clock,
            settings=settings,
        )
        self._recovered = False

    def drain_once(self) -> int:
        if not self._recovered:
            raise OrchestratorFatalError("必须先完成 readiness 与崩溃恢复，禁止领取新任务")
        self._status.heartbeat("queue_scan")
        claims = self._queue.claim(
            owner_token=self._owner_token,
            projects=None if self._projects is None else set(self._projects),
            limit=self._settings.max_concurrency,
            timeout_sec=self._settings.queue_op_timeout_sec,
        )
        if len(claims) > self._settings.max_concurrency:
            raise OrchestratorFatalError("queue 返回超出单并发上限的 claim")
        count = 0
        try:
            for claim in claims:
                self._dispatch(claim)
                count += 1
        finally:
            self._health.flush(
                Deadline.start(
                    self._settings.health_timeout_sec,
                    now=self._clock.monotonic(),
                )
            )
            self._status.heartbeat("idle")
        return count

    def recover_incomplete(self) -> None:
        if self._recovered:
            return
        readiness_deadline = Deadline.start(
            self._settings.readiness_timeout_sec,
            now=self._clock.monotonic(),
        )
        self._process.assert_ready(readiness_deadline)
        recovery_deadline = Deadline.start(
            self._settings.recovery_timeout_sec,
            now=self._clock.monotonic(),
        )
        self._health.recover(recovery_deadline)
        self._recovery.recover(self._recovery_owner_token)
        self._recovered = True

    def _dispatch(self, claim: ClaimedJob) -> AttemptResult | None:
        self._authorize_claim(claim)
        try:
            validate_target_commit(claim.job.meta.target_commit)
        except ValueError:
            rejected = self._queue.reject(
                claim,
                reason="target_commit 缺失或非法",
                timeout_sec=self._settings.queue_op_timeout_sec,
            )
            if not rejected:
                raise OrchestratorClaimLost("非法 target claim 已失权") from None
            return None
        decision = self._gate.evaluate(claim)
        if decision.disposition is DependencyDisposition.WAIT:
            retried = self._queue.retry(
                claim,
                reason=decision.reason,
                timeout_sec=self._settings.queue_op_timeout_sec,
            )
            if not retried:
                raise OrchestratorClaimLost("依赖等待 retry 未命中精确 claim")
            return None
        spec = self._factory.create(claim)
        if decision.disposition is DependencyDisposition.BLOCK:
            return self._dependency_block(claim, spec, decision)
        if decision.disposition is not DependencyDisposition.RUN:
            raise OrchestratorFatalError("依赖门禁返回未知决策")
        return self._execution.run(claim, spec, decision)

    def _authorize_claim(self, claim: ClaimedJob) -> None:
        if type(claim) is not ClaimedJob:
            raise OrchestratorFatalError("queue 返回了非法 claim 类型")
        if claim.owner_token != self._owner_token:
            raise OrchestratorFatalError("claim owner 与当前 worker 所有者不匹配")
        try:
            renewed = self._queue.renew(
                claim,
                ttl_sec=self._settings.startup_lease_ttl_sec,
                timeout_sec=self._settings.queue_op_timeout_sec,
            )
        except Exception as error:
            raise OrchestratorClaimLost("领取后首次续租失败") from error
        if not renewed:
            raise OrchestratorClaimLost("领取后首次续租未命中精确 claim")

    def _dependency_block(self, claim, spec, decision) -> AttemptResult:
        validated = validate_dependency_block(
            spec,
            decision,
            claim=claim,
            validated_at=self._clock.time(),
        )
        paths = self._artifacts.expected(spec.attempt_id)
        claimed = self._empty_entry(claim, spec, paths)
        self._journal.create(AttemptJournalRecord(claimed, spec=spec))
        initialized = self._artifacts.initialize(spec)
        if initialized != paths:
            raise OrchestratorFatalError("dependency BLOCK artifact 路径不一致")
        self._artifacts.write_result_once(paths, validated.result)
        checkpoint = AttemptFinalizationCheckpoint(
            1,
            spec.attempt_id,
            spec.fence,
            spec.target_commit,
            FinalizationAction.GUARDED_ACK,
            FinalizationEvidence.DEPENDENCY_BLOCK,
            attempt_result_digest(validated.result),
        )
        self._finalizer.finalize(
            claim=claim,
            spec=spec,
            paths=paths,
            base_entry=claimed,
            expected_phase=AttemptJournalPhase.CLAIMED,
            checkpoint=checkpoint,
            result=validated.result,
            validated=validated,
            death=None,
        )
        return validated.result

    def _empty_entry(
        self,
        claim: ClaimedJob,
        spec: AttemptSpec,
        paths: AttemptArtifactPaths,
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
            None,
            None,
            None,
            None,
            AttemptJournalPhase.CLAIMED.value,
            self._clock.time(),
            spec.timeout_sec,
        )


__all__ = [
    "AttemptOrchestrator",
    "OrchestratorClaimLost",
    "OrchestratorFatalError",
    "OrchestratorQuarantined",
]
