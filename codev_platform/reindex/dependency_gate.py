"""codegraph 依赖门禁与无进程阻断结果构造。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from codev_platform.index_manifest import (
    BuildRecord,
    latest_build,
    validate_manifest_busy_timeout,
)

from .attempts import (
    AttemptOutcome,
    AttemptResult,
    AttemptSpec,
    CanonicalJsonObject,
    ValidatedAttemptResult,
)
from .attempt_validation import _validated_dependency_block
from .queue_ports import (
    ClaimedJob,
    DependencyQueueState,
    DependencyQueueViewPort,
    validate_timeout,
)

_OID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_DEPENDENCIES = {"ingest": "codegraph", "code_vec": "codegraph"}


class DependencyDisposition(str, Enum):
    RUN = "run"
    WAIT = "wait"
    BLOCK = "block"


@dataclass(frozen=True, slots=True, init=False)
class DependencyDecision:
    """只包含可持久复算、无 claim 秘密的门禁事实。"""

    disposition: DependencyDisposition
    project_id: str
    dependent_kind: str
    dependency_kind: str | None
    target_commit: str
    proof: CanonicalJsonObject
    reason: str

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("DependencyDecision 只能由依赖门禁创建")

    def __post_init__(self) -> None:
        if type(self.disposition) is not DependencyDisposition:
            raise ValueError("disposition 必须是 DependencyDisposition")
        for field in ("project_id", "dependent_kind"):
            value = getattr(self, field)
            if type(value) is not str or not value.strip():
                raise ValueError(f"{field} 无效")
        if self.dependency_kind is not None and (
            type(self.dependency_kind) is not str or not self.dependency_kind.strip()
        ):
            raise ValueError("dependency_kind 无效")
        _target_commit(self.target_commit)
        if type(self.proof) is not CanonicalJsonObject:
            raise ValueError("proof 必须是 CanonicalJsonObject")
        if type(self.reason) is not str or not self.reason.strip():
            raise ValueError("reason 必须是非空字符串")
        if self.disposition is not DependencyDisposition.RUN and self.dependency_kind is None:
            raise ValueError("WAIT/BLOCK 必须指出依赖类型")
        _validate_decision_proof(self)


class DependencyGate(Protocol):
    def evaluate(self, claim: ClaimedJob) -> DependencyDecision: ...


def _target_commit(value: object) -> str:
    if type(value) is not str or _OID_RE.fullmatch(value) is None or set(value) == {"0"}:
        raise ValueError("依赖门禁 target_commit 必须是完整非零小写 OID")
    return value


def _validate_decision_proof(decision: DependencyDecision) -> None:
    payload = decision.proof.to_value()
    common = {
        "dependency_kind": decision.dependency_kind,
        "dependent_kind": decision.dependent_kind,
        "disposition": decision.disposition.value,
        "project_id": decision.project_id,
        "target_commit": decision.target_commit,
    }
    if any(payload.get(key) != value for key, value in common.items()):
        raise ValueError("依赖决策 proof 与决策身份不一致")
    if decision.disposition is not DependencyDisposition.BLOCK:
        return
    if set(payload) != {*common, "manifest", "queue_state"}:
        raise ValueError("BLOCK proof 字段集合不完整")
    manifest = payload.get("manifest")
    valid_manifest = (
        type(manifest) is dict
        and set(manifest) == {"attempt_id", "result_digest", "status", "target_commit"}
        and manifest.get("status") == "failed"
        and manifest.get("target_commit") == decision.target_commit
    )
    if not valid_manifest or payload.get("queue_state") != DependencyQueueState.ABSENT.value:
        raise ValueError("BLOCK proof 必须来自同目标 failed manifest 与 absent queue")


def _decision(**values: object) -> DependencyDecision:
    decision = object.__new__(DependencyDecision)
    for field in (
        "disposition",
        "project_id",
        "dependent_kind",
        "dependency_kind",
        "target_commit",
        "proof",
        "reason",
    ):
        object.__setattr__(decision, field, values[field])
    decision.__post_init__()
    return decision


def _manifest_fact(record: BuildRecord | None, target: str) -> tuple[str, dict[str, object]]:
    if record is None:
        return "missing", {
            "attempt_id": None,
            "result_digest": None,
            "status": "missing",
            "target_commit": None,
        }
    matched = record.target_commit == target
    if matched and record.status == "ok":
        status = "succeeded"
    elif matched and record.status == "failed":
        status = "failed"
    elif matched:
        status = "unknown"
    else:
        status = "other_target"
    return status, {
        "attempt_id": record.attempt_id,
        "result_digest": record.result_digest,
        "status": status,
        "target_commit": record.target_commit,
    }


def _proof(
    *,
    disposition: DependencyDisposition,
    project_id: str,
    dependent_kind: str,
    dependency_kind: str | None,
    target: str,
    manifest: dict[str, object] | None = None,
    queue_state: DependencyQueueState | None = None,
) -> CanonicalJsonObject:
    payload: dict[str, object] = {
        "dependency_kind": dependency_kind,
        "dependent_kind": dependent_kind,
        "disposition": disposition.value,
        "project_id": project_id,
        "target_commit": target,
    }
    if manifest is not None:
        payload["manifest"] = manifest
    if queue_state is not None:
        payload["queue_state"] = queue_state.value
    return CanonicalJsonObject.from_value(payload)


class ManifestDependencyGate:
    """把 manifest 完成事实与队列更新事实轻量聚合为三态决策。"""

    def __init__(
        self,
        *,
        manifest_path: Path,
        queue_view: DependencyQueueViewPort,
        queue_timeout_sec: float,
        manifest_busy_timeout_sec: float | None = None,
    ) -> None:
        self._manifest_path = Path(manifest_path)
        self._queue_view = queue_view
        self._queue_timeout_sec = validate_timeout(queue_timeout_sec)
        manifest_timeout = (
            self._queue_timeout_sec
            if manifest_busy_timeout_sec is None
            else manifest_busy_timeout_sec
        )
        self._manifest_busy_timeout_sec = validate_manifest_busy_timeout(manifest_timeout)

    def evaluate(self, claim: ClaimedJob) -> DependencyDecision:
        if type(claim) is not ClaimedJob:
            raise ValueError("依赖门禁只接受 ClaimedJob")
        target = _target_commit(claim.job.meta.target_commit)
        dependency = _DEPENDENCIES.get(claim.job.kind)
        if dependency is None:
            return _decision(
                disposition=DependencyDisposition.RUN,
                project_id=claim.job.project_id,
                dependent_kind=claim.job.kind,
                dependency_kind=None,
                target_commit=target,
                proof=_proof(
                    disposition=DependencyDisposition.RUN,
                    project_id=claim.job.project_id,
                    dependent_kind=claim.job.kind,
                    dependency_kind=None,
                    target=target,
                ),
                reason="当前索引类型没有前置依赖",
            )
        record = latest_build(
            claim.job.project_id,
            dependency,
            path=self._manifest_path,
            busy_timeout_sec=self._manifest_busy_timeout_sec,
        )
        manifest_state, manifest = _manifest_fact(record, target)
        if manifest_state == "succeeded":
            return _decision(
                disposition=DependencyDisposition.RUN,
                project_id=claim.job.project_id,
                dependent_kind=claim.job.kind,
                dependency_kind=dependency,
                target_commit=target,
                proof=_proof(
                    disposition=DependencyDisposition.RUN,
                    project_id=claim.job.project_id,
                    dependent_kind=claim.job.kind,
                    dependency_kind=dependency,
                    target=target,
                    manifest=manifest,
                ),
                reason="同目标 codegraph 已成功",
            )
        queue_state = self._dependency_state(claim, dependency, target)
        if manifest_state == "failed" and queue_state is DependencyQueueState.ABSENT:
            disposition = DependencyDisposition.BLOCK
            reason = "同目标 codegraph 已明确失败且没有更新工作"
        else:
            disposition = DependencyDisposition.WAIT
            reason = "前置 codegraph 尚未形成同目标成功事实"
        return _decision(
            disposition=disposition,
            project_id=claim.job.project_id,
            dependent_kind=claim.job.kind,
            dependency_kind=dependency,
            target_commit=target,
            proof=_proof(
                disposition=disposition,
                project_id=claim.job.project_id,
                dependent_kind=claim.job.kind,
                dependency_kind=dependency,
                target=target,
                manifest=manifest,
                queue_state=queue_state,
            ),
            reason=reason,
        )

    def _dependency_state(
        self,
        claim: ClaimedJob,
        dependency: str,
        target: str,
    ) -> DependencyQueueState:
        raw = self._queue_view.dependency_state(
            project_id=claim.job.project_id,
            kind=dependency,
            target_commit=target,
            timeout_sec=self._queue_timeout_sec,
        )
        try:
            return DependencyQueueState(raw)
        except (TypeError, ValueError):
            raise ValueError("依赖队列状态无效") from None


def validate_dependency_block(
    spec: AttemptSpec,
    decision: DependencyDecision,
    *,
    claim: ClaimedJob,
    validated_at: float,
) -> ValidatedAttemptResult:
    """把稳定 BLOCK 决策转换成规范 FAILED 结果，不生成进程事实。"""
    if type(decision) is not DependencyDecision:
        raise ValueError("依赖决策类型无效")
    if (
        decision.disposition is not DependencyDisposition.BLOCK
        or decision.project_id != spec.project_id
        or decision.dependent_kind != spec.kind
        or decision.target_commit != spec.target_commit
        or decision.dependency_kind is None
    ):
        raise ValueError("只有与 spec 同目标的 BLOCK 决策可生成失败结果")
    result = AttemptResult(
        schema_version=spec.schema_version,
        attempt_id=spec.attempt_id,
        fence=spec.fence,
        project_id=spec.project_id,
        kind=spec.kind,
        input_root="",
        target_commit=spec.target_commit,
        input_commits=(),
        input_trees=(),
        runtime_revision=spec.runtime_revision,
        outcome=AttemptOutcome.FAILED,
        rc=None,
        retryable=False,
        note=decision.reason,
        timing=(),
        log_ref=None,
        proof=CanonicalJsonObject.from_value({
            "dependency": decision.proof.to_value(),
            "evidence": "dependency_block",
            "success": False,
        }),
    )
    return _validated_dependency_block(
        spec,
        result,
        claim=claim,
        validated_at=validated_at,
    )


__all__ = [
    "DependencyDecision",
    "DependencyDisposition",
    "DependencyGate",
    "ManifestDependencyGate",
    "validate_dependency_block",
]
