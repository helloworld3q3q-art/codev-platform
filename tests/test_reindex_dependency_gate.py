"""依赖门禁与无进程阻断结果的确定性契约测试。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sqlite3
import time

import pytest

import codev_platform.reindex.attempt_validation as attempt_validation
import codev_platform.reindex.attempts as attempt_protocol
from codev_platform.index_manifest import BuildRecord, latest_build, record_build
from codev_platform.reindex.attempts import (
    AttemptOutcome,
    AttemptSpec,
    AttemptValidationEvidence,
    CanonicalJsonObject,
)
from codev_platform.reindex.dependency_gate import (
    DependencyDecision,
    DependencyDisposition,
    ManifestDependencyGate,
    validate_dependency_block,
)
from codev_platform.reindex.queue_ports import ClaimedJob, Job, JobMeta
from codev_platform.reindex.result_publisher import ResultPublisher

_TARGET = "a" * 40
_NEW_TARGET = "b" * 40
_RUNTIME = "c" * 64


@dataclass
class RecordingDependencyView:
    state: str = "absent"
    calls: list[tuple[str, str, str, float]] = field(default_factory=list)

    def dependency_state(
        self,
        *,
        project_id: str,
        kind: str,
        target_commit: str,
        timeout_sec: float,
    ) -> str:
        self.calls.append((project_id, kind, target_commit, timeout_sec))
        return self.state


def _claim(
    kind: str = "ingest",
    target: str = _TARGET,
    project_id: str = "demo",
) -> ClaimedJob:
    return ClaimedJob(
        job=Job(
            project_id=project_id,
            kind=kind,
            enqueued_at=1.0,
            meta=JobMeta(source="hook", target_commit=target),
        ),
        claim_token="claim-secret",
        owner_token="owner-1",
        lease_expires_at=100.0,
    )


def _spec(kind: str = "ingest", project_id: str = "demo") -> AttemptSpec:
    return AttemptSpec(
        schema_version=1,
        attempt_id="attempt-dependency-block",
        fence="fence-secret",
        project_id=project_id,
        kind=kind,
        input_kind="configured",
        input_payload=CanonicalJsonObject.from_value({"project_id": project_id}),
        target_commit=_TARGET,
        timeout_sec=30.0,
        runtime_revision=_RUNTIME,
    )


def _record(path: Path, *, status: str, target: str = _TARGET) -> None:
    record_build(
        BuildRecord(
            project_id="demo",
            kind="codegraph",
            git_commit=target,
            target_commit=target,
            status=status,
            attempt_id="dependency-attempt",
            result_digest="d" * 64,
        ),
        path=path,
    )


def _gate(path: Path, view: RecordingDependencyView) -> ManifestDependencyGate:
    return ManifestDependencyGate(
        manifest_path=path,
        queue_view=view,
        queue_timeout_sec=0.25,
    )


def test_无依赖_kind_直接运行且不读取队列(tmp_path: Path) -> None:
    view = RecordingDependencyView()

    decision = _gate(tmp_path / "manifest.sqlite", view).evaluate(_claim("chroma"))

    assert decision.disposition is DependencyDisposition.RUN
    assert decision.dependency_kind is None
    assert view.calls == []


def test_只有同目标_codegraph_成功才运行(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.sqlite"
    _record(manifest, status="ok")
    view = RecordingDependencyView(state="replacement")

    decision = _gate(manifest, view).evaluate(_claim())

    assert decision.disposition is DependencyDisposition.RUN
    assert decision.dependency_kind == "codegraph"
    assert view.calls == []
    assert decision.proof.to_value()["manifest"]["status"] == "succeeded"


@pytest.mark.parametrize("queue_state", ["active", "pending", "replacement"])
def test_依赖未成功且仍有可运行工作时等待并保留四值语义(
    tmp_path: Path,
    queue_state: str,
) -> None:
    manifest = tmp_path / "manifest.sqlite"
    _record(manifest, status="failed")
    view = RecordingDependencyView(state=queue_state)

    decision = _gate(manifest, view).evaluate(_claim())

    assert decision.disposition is DependencyDisposition.WAIT
    assert decision.proof.to_value()["queue_state"] == queue_state
    assert view.calls == [("demo", "codegraph", _TARGET, 0.25)]


def test_同目标明确失败且没有更新工作才稳定阻断(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.sqlite"
    _record(manifest, status="failed")
    view = RecordingDependencyView(state="absent")
    gate = _gate(manifest, view)

    first = gate.evaluate(_claim())
    second = gate.evaluate(_claim())

    assert first == second
    assert first.disposition is DependencyDisposition.BLOCK
    assert first.proof.to_value()["manifest"]["status"] == "failed"


def test_缺失或其他目标的_manifest_都等待而不误阻断(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing.sqlite"
    missing = _gate(missing_path, RecordingDependencyView()).evaluate(_claim())
    assert missing.disposition is DependencyDisposition.WAIT

    other_path = tmp_path / "other.sqlite"
    _record(other_path, status="ok", target=_NEW_TARGET)
    other = _gate(other_path, RecordingDependencyView()).evaluate(_claim())
    assert other.disposition is DependencyDisposition.WAIT


@pytest.mark.parametrize("queue_state", ["unknown", "", "ACTIVE"])
def test_队列视图返回未知状态时失败关闭(tmp_path: Path, queue_state: str) -> None:
    view = RecordingDependencyView(state=queue_state)

    with pytest.raises(ValueError, match="依赖队列状态"):
        _gate(tmp_path / "manifest.sqlite", view).evaluate(_claim())


def test_manifest_锁等待受独立有限预算约束(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.sqlite"
    _record(manifest, status="failed")
    blocker = sqlite3.connect(manifest)
    blocker.execute("BEGIN EXCLUSIVE")
    gate = ManifestDependencyGate(
        manifest_path=manifest,
        queue_view=RecordingDependencyView(),
        queue_timeout_sec=0.25,
        manifest_busy_timeout_sec=0.05,
    )
    started = time.monotonic()
    try:
        with pytest.raises(sqlite3.OperationalError):
            gate.evaluate(_claim())
    finally:
        blocker.rollback()
        blocker.close()

    assert time.monotonic() - started < 1.0


def test_依赖阻断发布明确无进程证据且不生成完成凭据(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.sqlite"
    _record(manifest, status="failed")
    claim = _claim()
    decision = _gate(manifest, RecordingDependencyView()).evaluate(claim)

    validated = validate_dependency_block(
        _spec(),
        decision,
        claim=claim,
        validated_at=20.0,
    )

    assert validated.evidence is AttemptValidationEvidence.DEPENDENCY_BLOCK
    assert validated.process_rc is None
    assert validated.result.outcome is AttemptOutcome.FAILED
    assert validated.result.rc is None
    assert validated.result.timing == ()
    assert validated.result.proof.to_value()["evidence"] == "dependency_block"

    receipt = ResultPublisher(tmp_path / "published.sqlite").publish(validated)
    assert receipt.published is True
    stored = latest_build("demo", "ingest", path=tmp_path / "published.sqlite")
    assert stored is not None
    assert stored.process_rc is None
    assert stored.validation_evidence == "dependency_block"
    assert stored.started_at is None
    assert stored.finished_at is None


@pytest.mark.parametrize(
    ("spec", "claim"),
    [
        (_spec(project_id="other"), _claim(project_id="other")),
        (_spec(kind="code_vec"), _claim(kind="code_vec")),
    ],
)
def test_block_决策不能跨项目或被依赖任务复用(
    tmp_path: Path,
    spec: AttemptSpec,
    claim: ClaimedJob,
) -> None:
    manifest = tmp_path / "manifest.sqlite"
    _record(manifest, status="failed")
    decision = _gate(manifest, RecordingDependencyView()).evaluate(_claim())

    with pytest.raises(ValueError, match="决策|身份|项目|任务"):
        validate_dependency_block(
            spec,
            decision,
            claim=claim,
            validated_at=20.0,
        )


def test_dependency_block_底层铸造器不从公共协议导出() -> None:
    assert not hasattr(attempt_protocol, "validate_dependency_block")
    assert "validate_dependency_block" not in attempt_validation.__all__
    assert "validate_attempt_result" in dir(attempt_protocol)
    assert "ValidatedAttemptResult" in attempt_protocol.__all__


def test_dependency_decision_不能绕过_gate_直接构造() -> None:
    with pytest.raises(TypeError, match="DependencyDecision|门禁"):
        DependencyDecision(
            DependencyDisposition.BLOCK,
            "demo",
            "ingest",
            "codegraph",
            _TARGET,
            CanonicalJsonObject.from_value({
                "dependency_kind": "codegraph",
                "dependent_kind": "ingest",
                "disposition": "block",
                "project_id": "demo",
                "target_commit": _TARGET,
            }),
            "手工伪造",
        )
