"""单一 reindex Orchestrator 的事件顺序与失败关闭测试。"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from codev_platform.reindex.attempt_process import (
    AttemptProcessStartError,
    RecoveryReport,
    RecoveryState,
    build_process_identity,
)
from codev_platform.reindex.attempts import AttemptOutcome, ConfirmedProcessDeath
from codev_platform.reindex.orchestrator import (
    OrchestratorFatalError,
    OrchestratorQuarantined,
)
from codev_platform.reindex.orchestrator_models import AttemptJournalPhase
from codev_platform.reindex.queue_ports import ClaimedJob, Job, JobMeta
from tests.reindex_orchestrator_support import (
    RecordingStatus,
    _TARGET,
    _claim,
    _orchestrator,
    _settings,
    _spec,
)


def test_run_严格遵守启动与最终化顺序(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, _process, _journal, _artifacts, _queue, _publisher, _health = (
        _orchestrator(
            tmp_path,
            events,
        )
    )

    result = orchestrator._dispatch(claim)

    assert result.outcome is AttemptOutcome.SUCCEEDED
    ordered = [
        "queue:renew",
        "factory:create",
        "queue:renew",
        "artifacts:expected",
        "journal:claimed",
        "artifacts:initialize",
        "journal:preparing",
        "process:prepare",
        "journal:executing",
        "queue:renew",
        "process:activate",
        "process:poll",
        "process:confirm_dead",
        "artifacts:load_validated",
        "journal:finalizing",
        "queue:renew",
        "input:cleanup",
        "queue:renew",
        "queue:begin_publish",
        "publisher:publish",
        "queue:ack",
        "queue:end_publish",
        "artifacts:cleanup",
        "journal:clear",
        "health:request:demo",
        "health:flush",
    ]
    assert [event for event in events if event in ordered] == ordered


def test_handle_journal_保存失败会终止且绝不_activate(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, _process, journal, _artifacts, _queue, _publisher, _health = _orchestrator(
        tmp_path,
        events,
    )
    journal.fail_phase = AttemptJournalPhase.EXECUTING

    orchestrator._dispatch(claim)

    assert "process:terminate" in events
    assert "process:activate" not in events
    assert events.index("process:terminate") < events.index("queue:retry")


def test_死亡无法确认时_quarantine_且绝不清理输入(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, process, journal, _artifacts, _queue, _publisher, _health = _orchestrator(
        tmp_path,
        events,
    )
    journal.fail_phase = AttemptJournalPhase.EXECUTING
    process.confirmed = False

    with pytest.raises(OrchestratorQuarantined):
        orchestrator._dispatch(claim)

    assert "journal:quarantined" in events
    assert "queue:quarantine" in events
    assert "input:cleanup" not in events
    assert "artifacts:cleanup" not in events


def test_malformed_target_在_factory_journal_process_之前_reject(tmp_path: Path) -> None:
    events: list[str] = []
    claim = _claim(target=None)
    orchestrator, _claim_value, _process, _journal, _artifacts, _queue, _publisher, _health = (
        _orchestrator(
            tmp_path,
            events,
            claims=[claim],
        )
    )

    orchestrator.recover_incomplete()
    events.clear()

    assert orchestrator.drain_once() == 1
    assert events[:3] == ["queue:claim", "queue:renew", "queue:reject"]
    assert "factory:create" not in events
    assert not any(event.startswith("journal:") for event in events)
    assert "process:prepare" not in events


def test_dependency_wait_在_factory_journal_process_之前_retry(tmp_path: Path) -> None:
    events: list[str] = []
    claim = _claim()
    orchestrator, _claim_value, _process, _journal, _artifacts, _queue, _publisher, _health = (
        _orchestrator(
            tmp_path,
            events,
            claims=[claim],
        )
    )
    # 缺少 codegraph manifest 且队列无依赖工作，应稳定 WAIT。
    claim = ClaimedJob(
        Job("demo", "ingest", 1.0, meta=JobMeta(target_commit=_TARGET)),
        claim.claim_token,
        claim.owner_token,
        claim.lease_expires_at,
    )
    orchestrator._queue.claims = [claim]

    orchestrator.recover_incomplete()
    events.clear()

    assert orchestrator.drain_once() == 1
    assert "queue:retry" in events
    assert "factory:create" not in events
    assert "process:prepare" not in events


def test_timeout_续租杀树后只_retry_不读取或发布结果(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, process, _journal, _artifacts, _queue, _publisher, _health = _orchestrator(
        tmp_path,
        events,
    )
    process.poll_values = [None, None, None]
    short = dataclasses.replace(_spec(), timeout_sec=0.02)
    orchestrator._factory.spec = short

    result = orchestrator._dispatch(claim)

    assert result.outcome is AttemptOutcome.RETRYABLE
    assert events.index("queue:renew") < events.index("process:terminate")
    assert events.index("process:terminate") < events.index("queue:retry")
    assert "artifacts:load_validated" not in events
    assert "publisher:publish" not in events


def test_raw_result_没有_completion_receipt_只能在死亡后_retry(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, _process, _journal, artifacts, _queue, _publisher, _health = _orchestrator(
        tmp_path,
        events,
    )
    artifacts.validated = ValueError("缺少 completion receipt")

    result = orchestrator._dispatch(claim)

    assert result.outcome is AttemptOutcome.RETRYABLE
    assert events.index("process:confirm_dead") < events.index("queue:retry")
    assert "publisher:publish" not in events


def test_guard_发现新目标时_ack_但不发布旧结果(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, _process, _journal, _artifacts, queue, _publisher, _health = _orchestrator(
        tmp_path,
        events,
    )
    queue.superseded = True

    orchestrator._dispatch(claim)

    assert "queue:ack" in events
    assert "publisher:publish" not in events


def test_manifest_发布失败保留_finalizing_且不释放_queue(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, _process, journal, _artifacts, _queue, publisher, _health = _orchestrator(
        tmp_path,
        events,
    )
    publisher.published = False

    with pytest.raises(RuntimeError, match="manifest"):
        orchestrator._dispatch(claim)

    assert journal.record is not None
    assert journal.record.phase is AttemptJournalPhase.FINALIZING
    assert "queue:ack" not in events
    assert "artifacts:cleanup" not in events
    assert "journal:clear" not in events


def test_health_flush异常不得反向锁住已完成的attempt终态(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, _process, journal, _artifacts, _queue, _publisher, health = _orchestrator(
        tmp_path,
        events,
    )
    health.flush_error = OSError("health 输出不可写")

    with pytest.raises(OSError, match="health"):
        orchestrator._dispatch(claim)

    assert journal.record is None
    assert "queue:ack" in events
    assert "artifacts:cleanup" in events
    assert "journal:clear" in events
    assert "health:request:demo" in events
    assert "health:flush" in events
    assert events.index("journal:clear") < events.index("health:request:demo")


def test_health_flush目标成功但存在非关键降级时仍清理journal(tmp_path: Path) -> None:
    """终态 health 是观测刷新；目标项目已完成时，WARN/降级不得造成 FINALIZING 自锁。"""
    events: list[str] = []
    orchestrator, claim, _process, journal, _artifacts, _queue, _publisher, health = _orchestrator(
        tmp_path,
        events,
    )
    health.attempted_projects = ("demo", "demo-optional")
    health.failed_projects = ("demo-optional",)
    health.succeeded_projects = ("demo",)

    orchestrator._dispatch(claim)

    assert "health:request:demo" in events
    assert "health:flush" in events
    assert "journal:clear" in events
    assert journal.record is None


def test_health_flush目标已尝试但失败时仍清理journal并交给health状态承载(tmp_path: Path) -> None:
    """health 失败是可观测降级，不应阻塞已完成的 queue/manifest 收口。"""
    events: list[str] = []
    orchestrator, claim, _process, journal, _artifacts, _queue, _publisher, health = _orchestrator(
        tmp_path,
        events,
    )
    health.attempted_projects = ("demo",)
    health.succeeded_projects = ()
    health.failed_projects = ("demo",)

    orchestrator._dispatch(claim)

    assert "health:request:demo" in events
    assert "health:flush" in events
    assert "journal:clear" in events
    assert journal.record is None


def test_health_flush未尝试目标时停止后续工作但不恢复attempt终态(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, _process, journal, _artifacts, _queue, _publisher, health = _orchestrator(
        tmp_path,
        events,
    )
    health.attempted_projects = ()
    health.succeeded_projects = ()

    with pytest.raises(OrchestratorFatalError, match="未尝试|保留"):
        orchestrator._dispatch(claim)

    assert journal.record is None
    assert "health:request:demo" in events
    assert "health:flush" in events
    assert "journal:clear" in events
    assert events.index("journal:clear") < events.index("health:request:demo")


def test_finalizing_续租失败时不清理输入也不释放_queue(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, _process, journal, _artifacts, queue, _publisher, _health = _orchestrator(
        tmp_path,
        events,
    )
    queue.renew_results = [True, True, True, False]

    with pytest.raises(RuntimeError, match="续租"):
        orchestrator._dispatch(claim)

    assert journal.record is not None
    assert journal.record.phase is AttemptJournalPhase.FINALIZING
    assert "input:cleanup" not in events
    assert "queue:retry" not in events
    assert "queue:begin_publish" not in events


def test_run_在任何副作用前拒绝_claim_spec_身份不匹配(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, _process, _journal, _artifacts, _queue, _publisher, _health = (
        _orchestrator(
            tmp_path,
            events,
        )
    )

    orchestrator._factory.spec = dataclasses.replace(_spec(), kind="ingest")

    with pytest.raises(ValueError, match="身份"):
        orchestrator._dispatch(claim)

    assert "process:prepare" not in events
    assert not any(event.startswith("journal:") for event in events)


def test_queue_违反单并发返回多_claim_时失败关闭(tmp_path: Path) -> None:
    events: list[str] = []
    claims = [_claim(), _claim()]
    orchestrator, *_rest = _orchestrator(tmp_path, events, claims=claims)

    orchestrator.recover_incomplete()
    events.clear()

    with pytest.raises(RuntimeError, match="单并发"):
        orchestrator.drain_once()

    assert "factory:create" not in events
    assert "process:prepare" not in events


def test_heartbeat_与_lease_由同一控制循环_tick(tmp_path: Path) -> None:
    events: list[str] = []
    status = RecordingStatus(events)
    settings = dataclasses.replace(_settings(), heartbeat_sec=0.01, renew_sec=0.01)
    orchestrator, claim, process, _journal, _artifacts, _queue, _publisher, _health = _orchestrator(
        tmp_path,
        events,
        settings=settings,
        status=status,
    )
    process.poll_values = [None, None, 0]

    orchestrator._dispatch(claim)

    second_heartbeat = [index for index, value in enumerate(events) if value == "status:executing"][
        1
    ]
    renew = next(
        index
        for index, value in enumerate(events)
        if value == "queue:renew" and index > second_heartbeat
    )
    assert second_heartbeat < renew


def test_执行中状态写失败必须先杀树再安全_retry(tmp_path: Path) -> None:
    events: list[str] = []
    status = RecordingStatus(events)
    status.error_phase = AttemptJournalPhase.EXECUTING.value
    orchestrator, claim, process, _journal, _artifacts, _queue, _publisher, _health = _orchestrator(
        tmp_path,
        events,
        status=status,
    )
    process.poll_values = [None]

    result = orchestrator._dispatch(claim)

    assert result is not None and result.outcome is AttemptOutcome.RETRYABLE
    assert "process:terminate" in events
    assert events.index("process:terminate") < events.index("queue:retry")


def test_queue_scan状态写失败必须发生在任何claim之前(tmp_path: Path) -> None:
    events: list[str] = []
    status = RecordingStatus(events)
    status.error_phase = "queue_scan"
    claim = _claim()
    orchestrator, _claim_value, _process, _journal, _artifacts, _queue, _publisher, _health = (
        _orchestrator(
            tmp_path,
            events,
            claims=[claim],
            status=status,
        )
    )
    orchestrator.recover_incomplete()
    events.clear()

    with pytest.raises(OSError, match="状态端口"):
        orchestrator.drain_once()

    assert events == ["status:queue_scan"]


def test_orchestrator_不暴露绕过依赖门禁的_run入口(tmp_path: Path) -> None:
    orchestrator, *_rest = _orchestrator(tmp_path, [])

    assert not hasattr(orchestrator, "run")


def test_错误_owner_在门禁_factory_journal和进程前失败关闭(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, *_rest = _orchestrator(tmp_path, events)
    foreign = dataclasses.replace(claim, owner_token="owner-other")

    with pytest.raises(OrchestratorFatalError, match="owner|所有者"):
        orchestrator._dispatch(foreign)

    assert events == []


def test_启动前首次精确续租失败时零_factory_journal和进程(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, _process, _journal, _artifacts, queue, *_rest = _orchestrator(
        tmp_path,
        events,
    )
    queue.renew_results = [False]

    with pytest.raises(RuntimeError, match="续租|lease"):
        orchestrator._dispatch(claim)

    assert events == ["queue:renew"]


def test_prepare_未知异常必须恢复隔离而不能直接_retry(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, process, journal, _artifacts, _queue, *_rest = _orchestrator(
        tmp_path,
        events,
    )
    process.prepare_error = RuntimeError("模拟创建后丢失返回值")
    process.confirmed = False

    with pytest.raises(OrchestratorQuarantined):
        orchestrator._dispatch(claim)

    assert "journal:preparing" in events
    assert "process:recover" in events
    assert "queue:retry" not in events
    assert journal.record is not None
    assert journal.record.phase is AttemptJournalPhase.QUARANTINED


def test_prepare_明确未启动且可重试才进入_no_process_finalizing(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, process, _journal, _artifacts, _queue, *_rest = _orchestrator(
        tmp_path,
        events,
    )
    process.prepare_error = AttemptProcessStartError(
        handle=None,
        death_proof=None,
        retryable=True,
        note="测试准备前失败",
    )
    process.recovery_report = RecoveryReport(
        RecoveryState.NEVER_STARTED,
        None,
        None,
        "确定性 containment 不存在",
    )

    result = orchestrator._dispatch(claim)

    assert result is not None and result.outcome is AttemptOutcome.RETRYABLE
    assert events.index("process:recover") < events.index("journal:finalizing")
    assert "queue:retry" in events
    assert "process:activate" not in events


def test_prepare_非重试错误即使未启动也不进入_retry(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, process, journal, _artifacts, _queue, *_rest = _orchestrator(
        tmp_path,
        events,
    )
    process.prepare_error = AttemptProcessStartError(
        handle=None,
        death_proof=None,
        retryable=False,
        note="测试永久配置错误",
    )
    process.recovery_report = RecoveryReport(
        RecoveryState.NEVER_STARTED,
        None,
        None,
        "确定性 containment 不存在",
    )

    with pytest.raises(OrchestratorFatalError, match="不可重试|prepare"):
        orchestrator._dispatch(claim)

    assert "queue:retry" not in events
    assert journal.record is not None
    assert journal.record.phase is AttemptJournalPhase.PREPARING


def test_错误死亡证明不能清理输入或释放_queue(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, process, journal, _artifacts, _queue, *_rest = _orchestrator(
        tmp_path,
        events,
    )
    wrong_ref = "wrong-job-ref"
    wrong_identity = build_process_identity(
        pid=999,
        native_ref=wrong_ref,
        birth_marker="wrong-birth",
    )
    process.proof_override = ConfirmedProcessDeath(
        wrong_identity,
        "wrong-containment",
        20.0,
        "错误进程已死",
    )

    with pytest.raises(OrchestratorQuarantined):
        orchestrator._dispatch(claim)

    assert "input:cleanup" not in events
    assert "queue:ack" not in events
    assert "queue:retry" not in events
    assert journal.record is not None
    assert journal.record.phase is AttemptJournalPhase.QUARANTINED


def test_quarantine_journal_写失败仍必须先落_queue_quarantine(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, process, journal, _artifacts, _queue, *_rest = _orchestrator(
        tmp_path,
        events,
    )
    process.poll_values = [None, None]
    process.terminate_error = OSError("测试终止报告丢失")
    journal.fail_phase = AttemptJournalPhase.QUARANTINED
    orchestrator._factory.spec = dataclasses.replace(_spec(), timeout_sec=0.001)

    with pytest.raises(OrchestratorQuarantined):
        orchestrator._dispatch(claim)

    assert "journal:quarantined" in events
    assert "queue:quarantine" in events
    assert events.index("queue:quarantine") < events.index("journal:quarantined")
    assert "input:cleanup" not in events


def test_错配_handle_quarantine必须持久保留完整进程引用(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, process, journal, _artifacts, _queue, *_rest = _orchestrator(
        tmp_path,
        events,
    )
    foreign_ref = "foreign-job-ref"
    process.handle = dataclasses.replace(
        process.handle,
        attempt_id="attempt-foreign",
        pid=456,
        native_ref=foreign_ref,
        process_identity=build_process_identity(
            pid=456,
            native_ref=foreign_ref,
            birth_marker="foreign-birth",
        ),
    )

    with pytest.raises(OrchestratorQuarantined):
        orchestrator._dispatch(claim)

    assert journal.record is not None
    assert journal.record.phase is AttemptJournalPhase.QUARANTINED
    assert journal.record.entry.process_identity == process.handle.process_identity
    assert journal.record.entry.native_ref == process.handle.native_ref
