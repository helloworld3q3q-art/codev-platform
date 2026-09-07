"""编排器测试共享替身与构造器。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from codev_platform.reindex.attempt_artifacts import AttemptArtifactPaths
from codev_platform.reindex.attempt_process import (
    Deadline,
    ExecutionHandle,
    RecoveryReport,
    RecoveryState,
    TerminationReport,
    build_process_identity,
)
from codev_platform.reindex.attempts import (
    AttemptOutcome,
    AttemptResult,
    AttemptSpec,
    CanonicalJsonObject,
    CleanupReport,
    ConfirmedProcessDeath,
    validate_attempt_result,
)
from codev_platform.reindex.dependency_gate import ManifestDependencyGate
from codev_platform.reindex.health_refresh import HealthRefreshReport
from codev_platform.reindex.orchestrator import AttemptOrchestrator
from codev_platform.reindex.orchestrator_models import (
    AttemptJournalPhase,
    AttemptJournalRecord,
    OrchestratorSettings,
)
from codev_platform.reindex.queue_ports import (
    ClaimedJob,
    Job,
    JobMeta,
    QuarantineRecord,
)
from codev_platform.reindex.result_publisher import PublishReceipt

_TARGET = "a" * 40
_RUNTIME = "c" * 64


class FakeClock:
    def __init__(self) -> None:
        self.mono = 0.0
        self.wall = 10.0

    def monotonic(self) -> float:
        return self.mono

    def time(self) -> float:
        return self.wall

    def wait(self, timeout_sec: float) -> None:
        self.mono += timeout_sec
        self.wall += timeout_sec


class RecordingPermit:
    def __init__(self, events: list[str], *, superseded: bool = False) -> None:
        self._events = events
        self._superseded = superseded

    @property
    def superseded(self) -> bool:
        return self._superseded

    def ack(self) -> bool:
        self._events.append("queue:ack")
        return True


class RecordingQueue:
    def __init__(self, events: list[str], claims: list[ClaimedJob] | None = None) -> None:
        self.events = events
        self.claims = list(claims or [])
        self.renewed = True
        self.renew_results: list[bool] = []
        self.superseded = False
        self.recovered_claims: list[ClaimedJob] = []

    def claim(self, **_kwargs: object) -> list[ClaimedJob]:
        self.events.append("queue:claim")
        claims, self.claims = self.claims, []
        return claims

    def renew(self, _claim: ClaimedJob, *, ttl_sec: float, timeout_sec: float) -> bool:
        assert ttl_sec > 0 and timeout_sec > 0
        self.events.append("queue:renew")
        if self.renew_results:
            return self.renew_results.pop(0)
        return self.renewed

    def retry(self, _claim: ClaimedJob, *, reason: str, timeout_sec: float) -> bool:
        assert reason and timeout_sec > 0
        self.events.append("queue:retry")
        return True

    def reject(self, _claim: ClaimedJob, *, reason: str, timeout_sec: float) -> bool:
        assert reason and timeout_sec > 0
        self.events.append("queue:reject")
        return True

    def quarantine(self, claim: ClaimedJob, **_kwargs: object):
        self.events.append("queue:quarantine")
        return QuarantineRecord(
            claim.job.project_id,
            claim.job.kind,
            claim.claim_token,
            str(_kwargs["attempt_id"]),
            str(_kwargs["fence"]),
            str(_kwargs["process_identity"]),
            str(_kwargs["containment_kind"]),
            str(_kwargs["native_ref"]),
            str(_kwargs["reason"]),
            10.0,
        )

    @contextmanager
    def begin_publish(self, _claim: ClaimedJob, **_kwargs: object) -> Iterator[RecordingPermit]:
        self.events.append("queue:begin_publish")
        yield RecordingPermit(self.events, superseded=self.superseded)
        self.events.append("queue:end_publish")

    def dependency_state(self, *_args: object, **_kwargs: object) -> str:
        self.events.append("queue:dependency_state")
        return "absent"

    def recover_owned(self, **_kwargs: object) -> list[ClaimedJob]:
        self.events.append("queue:recover_owned")
        return list(self.recovered_claims)


class RecordingProcess:
    def __init__(self, events: list[str], clock: FakeClock) -> None:
        self.events = events
        self.clock = clock
        self.poll_values: list[int | None] = [0]
        self.confirmed = True
        self.prepare_error: Exception | None = None
        self.confirm_error: Exception | None = None
        self.terminate_error: Exception | None = None
        self.proof_override: ConfirmedProcessDeath | None = None
        self.recovery_report: RecoveryReport | None = None
        native_ref = "job-ref"
        self.handle = ExecutionHandle(
            attempt_id="attempt-1",
            pid=123,
            process_identity=build_process_identity(
                pid=123,
                native_ref=native_ref,
                birth_marker="birth-1",
            ),
            containment_kind="test-job",
            native_ref=native_ref,
            started_at=clock.time(),
        )

    def prepare(self, **_kwargs: object) -> ExecutionHandle:
        self.events.append("process:prepare")
        if self.prepare_error is not None:
            raise self.prepare_error
        return self.handle

    def assert_ready(self, _deadline: Deadline) -> None:
        self.events.append("process:assert_ready")

    def activate(self, _handle: ExecutionHandle, _deadline: Deadline) -> None:
        self.events.append("process:activate")

    def poll(self, _handle: ExecutionHandle) -> int | None:
        self.events.append("process:poll")
        return self.poll_values.pop(0) if self.poll_values else 0

    def _proof(self) -> ConfirmedProcessDeath:
        return ConfirmedProcessDeath(
            self.handle.process_identity,
            self.handle.containment_kind,
            max(self.clock.time(), self.handle.started_at),
            "测试进程树已空",
        )

    def confirm_dead(self, _handle: ExecutionHandle, _deadline: Deadline):
        self.events.append("process:confirm_dead")
        if self.confirm_error is not None:
            raise self.confirm_error
        return self.proof_override or (self._proof() if self.confirmed else None)

    def terminate(
        self,
        _handle: ExecutionHandle,
        *,
        grace_sec: float,
        deadline: Deadline,
    ) -> TerminationReport:
        self.events.append("process:terminate")
        if self.terminate_error is not None:
            raise self.terminate_error
        requested = self.clock.time()
        proof = self.proof_override or (self._proof() if self.confirmed else None)
        return TerminationReport(
            requested_at=requested,
            finished_at=requested,
            graceful=False,
            forced=True,
            confirmed_dead=proof is not None,
            death_proof=proof,
            note="测试终止",
        )

    def recover(self, _entry: object, _deadline: Deadline) -> RecoveryReport:
        self.events.append("process:recover")
        return self.recovery_report or RecoveryReport(
            RecoveryState.ACTIVE,
            self.handle,
            None,
            "测试恢复",
        )

    def recover_handle(self, _handle: ExecutionHandle, _deadline: Deadline) -> RecoveryReport:
        self.events.append("process:recover_handle")
        return self.recovery_report or RecoveryReport(
            RecoveryState.ACTIVE,
            self.handle,
            None,
            "测试恢复",
        )


class RecordingArtifacts:
    def __init__(
        self,
        events: list[str],
        root: Path,
        spec: AttemptSpec,
        validated,
    ) -> None:
        self.events = events
        self.paths = AttemptArtifactPaths(
            root=root,
            spec=root / "spec.json",
            result=root / "result.json",
            receipt=root / "completion.json",
            bootstrap_log=root / "bootstrap.log",
        )
        self.validated = validated
        self.spec = spec
        self.spec_error: Exception | None = None

    def expected(self, _attempt_id: str) -> AttemptArtifactPaths:
        self.events.append("artifacts:expected")
        return self.paths

    def initialize(self, _spec: AttemptSpec) -> AttemptArtifactPaths:
        self.events.append("artifacts:initialize")
        return self.paths

    def verify_journal(self, _entry) -> AttemptArtifactPaths:
        self.events.append("artifacts:verify_journal")
        return self.paths

    def read_spec(self, _paths: AttemptArtifactPaths) -> AttemptSpec:
        self.events.append("artifacts:read_spec")
        if self.spec_error is not None:
            raise self.spec_error
        return self.spec

    def read_result(self, _paths: AttemptArtifactPaths) -> AttemptResult | None:
        self.events.append("artifacts:read_result")
        if isinstance(self.validated, BaseException):
            raise self.validated
        return self.validated.result

    def load_validated(self, _paths: AttemptArtifactPaths, _claim: ClaimedJob):
        self.events.append("artifacts:load_validated")
        if isinstance(self.validated, BaseException):
            raise self.validated
        return self.validated

    def write_result_once(self, _paths: AttemptArtifactPaths, _result: AttemptResult) -> None:
        self.events.append("artifacts:write_result")

    def cleanup(self, _paths: AttemptArtifactPaths) -> None:
        self.events.append("artifacts:cleanup")


class MemoryJournal:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.record: AttemptJournalRecord | None = None
        self.fail_phase: AttemptJournalPhase | None = None

    def load(self) -> AttemptJournalRecord | None:
        return self.record

    def create(self, record: AttemptJournalRecord) -> None:
        self.events.append(f"journal:{record.phase.value}")
        if self.record is not None or record.phase is not AttemptJournalPhase.CLAIMED:
            raise OSError("journal create CAS 失败")
        if record.phase is self.fail_phase:
            raise OSError("注入 journal 保存失败")
        self.record = record

    def transition(
        self,
        record: AttemptJournalRecord,
        *,
        expected: AttemptJournalPhase,
    ) -> None:
        from codev_platform.reindex.orchestrator_models import validate_journal_transition

        self.events.append(f"journal:{record.phase.value}")
        if self.record is None or self.record.phase is not expected:
            raise OSError("journal transition CAS 失败")
        validate_journal_transition(expected, record.phase)
        if record.phase is self.fail_phase:
            raise OSError("注入 journal 保存失败")
        self.record = record

    def clear(
        self,
        *,
        attempt_id: str,
        fence: str,
        expected: AttemptJournalPhase,
    ) -> None:
        assert self.record is not None
        assert self.record.phase is expected
        assert (attempt_id, fence) == (
            self.record.entry.attempt_id,
            self.record.entry.fence,
        )
        self.events.append("journal:clear")
        self.record = None


class RecordingCleanup:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def release(
        self,
        spec: AttemptSpec,
        _death: ConfirmedProcessDeath,
        _deadline: Deadline,
    ) -> CleanupReport:
        self.events.append("input:cleanup")
        return CleanupReport(True, spec.attempt_id, "完成")

    def release_unstarted(
        self,
        spec: AttemptSpec,
        _deadline: Deadline,
    ) -> CleanupReport:
        self.events.append("input:cleanup_unstarted")
        return CleanupReport(True, spec.attempt_id, "完成")


class RecordingPublisher:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.published = True

    def publish(self, validated) -> PublishReceipt:
        from codev_platform.reindex.attempt_completion import attempt_result_digest

        self.events.append("publisher:publish")
        return PublishReceipt(
            self.published,
            validated.result.attempt_id,
            attempt_result_digest(validated.result) if self.published else None,
            "完成",
        )


class RecordingHealth:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.flush_error: Exception | None = None
        self.attempted_projects: tuple[str, ...] = ("demo",)
        self.failed_projects: tuple[str, ...] = ()
        self.succeeded_projects: tuple[str, ...] = ("demo",)

    def request(self, project_id: str) -> None:
        self.events.append(f"health:request:{project_id}")

    def flush(self, _deadline: Deadline) -> HealthRefreshReport:
        self.events.append("health:flush")
        if self.flush_error is not None:
            raise self.flush_error
        return HealthRefreshReport(
            attempted_projects=self.attempted_projects,
            failed_projects=self.failed_projects,
            succeeded_projects=self.succeeded_projects,
            containment_confirmed_dead=True,
        )

    def recover(self, _deadline: Deadline) -> None:
        self.events.append("health:recover")


class RecordingFactory:
    def __init__(self, events: list[str], spec: AttemptSpec) -> None:
        self.events = events
        self.spec = spec

    def create(self, _claim: ClaimedJob) -> AttemptSpec:
        self.events.append("factory:create")
        return self.spec


class RecordingStatus:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.error_phase: str | None = None

    def heartbeat(self, phase: str, _claim: object | None = None) -> None:
        self.events.append(f"status:{phase}")
        if phase == self.error_phase:
            raise OSError("注入状态端口写失败")


def _claim(*, target: str | None = _TARGET) -> ClaimedJob:
    return ClaimedJob(
        Job(
            "demo",
            "chroma",
            1.0,
            meta=JobMeta(source="test", target_commit=target),
        ),
        "claim-secret",
        "owner-1",
        100.0,
    )


def _spec() -> AttemptSpec:
    return AttemptSpec(
        1,
        "attempt-1",
        "fence-secret",
        "demo",
        "chroma",
        "configured",
        CanonicalJsonObject.from_value({"project_id": "demo"}),
        _TARGET,
        30.0,
        _RUNTIME,
    )


def _validated(spec: AttemptSpec, claim: ClaimedJob):
    result = AttemptResult(
        1,
        spec.attempt_id,
        spec.fence,
        spec.project_id,
        spec.kind,
        "/input",
        spec.target_commit,
        (("main", _TARGET),),
        (("main", "d" * 40),),
        spec.runtime_revision,
        AttemptOutcome.SUCCEEDED,
        0,
        False,
        "完成",
        (("started_at", 10.0), ("finished_at", 11.0)),
        "logs/attempt.log",
        CanonicalJsonObject.from_value({"success": True}),
    )
    return validate_attempt_result(
        spec,
        result,
        claim=claim,
        process_rc=0,
        validated_at=12.0,
    )


def _settings() -> OrchestratorSettings:
    return OrchestratorSettings(
        poll_sec=0.01,
        queue_op_timeout_sec=0.2,
        heartbeat_sec=1.0,
        renew_sec=1.0,
        lease_ttl_sec=5.0,
        kill_grace_sec=0.0,
        kill_timeout_sec=1.0,
        cleanup_timeout_sec=1.0,
        max_concurrency=1,
    )


def _orchestrator(
    tmp_path: Path,
    events: list[str],
    *,
    claims=None,
    settings: OrchestratorSettings | None = None,
    status: RecordingStatus | None = None,
):
    clock = FakeClock()
    claim = _claim()
    spec = _spec()
    queue = RecordingQueue(events, claims=claims)
    process = RecordingProcess(events, clock)
    journal = MemoryJournal(events)
    artifacts = RecordingArtifacts(
        events,
        tmp_path / "artifacts",
        spec,
        _validated(spec, claim),
    )
    gate = ManifestDependencyGate(
        manifest_path=tmp_path / "manifest.sqlite",
        queue_view=queue,
        queue_timeout_sec=0.2,
        manifest_busy_timeout_sec=0.2,
    )
    publisher = RecordingPublisher(events)
    health = RecordingHealth(events)
    orchestrator = AttemptOrchestrator(
        queue=queue,
        process_backend=process,
        artifacts=artifacts,
        dependency_gate=gate,
        publisher=publisher,
        journal=journal,
        cleanup_router=RecordingCleanup(events),
        spec_factory=RecordingFactory(events, spec),
        observer_argv=lambda _paths: ("C:/python.exe", "-m", "observer"),
        health_refresh=health,
        clock=clock,
        settings=settings or _settings(),
        owner_token="owner-1",
        projects={"demo"},
        cwd=tmp_path,
        status=status,
    )
    return orchestrator, claim, process, journal, artifacts, queue, publisher, health
