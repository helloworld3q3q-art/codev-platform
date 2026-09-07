"""把已验证 attempt 结果精确、幂等地发布到统一 manifest。"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path

from codev_platform.index_manifest import (
    BuildRecord,
    DEFAULT_MANIFEST_BUSY_TIMEOUT_SEC,
    ManifestPublishOutcome,
    publish_build,
    validate_manifest_busy_timeout,
)

from .attempt_completion import attempt_result_digest
from .attempts import (
    AttemptOutcome,
    AttemptResult,
    AttemptSpec,
    ValidatedAttemptResult,
)

_TERMINAL_OUTCOMES = {AttemptOutcome.SUCCEEDED, AttemptOutcome.FAILED}


@dataclass(frozen=True, slots=True)
class PublishReceipt:
    """供 Orchestrator 决定是否允许后续 ack 的无秘密回执。"""

    published: bool
    attempt_id: str
    result_digest: str | None
    note: str

    def __post_init__(self) -> None:
        if type(self.published) is not bool:
            raise ValueError("published 必须是 bool")
        if type(self.attempt_id) is not str or not self.attempt_id.strip():
            raise ValueError("attempt_id 必须是非空字符串")
        if self.result_digest is not None:
            valid_digest = (
                type(self.result_digest) is str
                and len(self.result_digest) == 64
                and all(char in "0123456789abcdef" for char in self.result_digest)
            )
            if not valid_digest:
                raise ValueError("result_digest 必须是小写 SHA-256 或 None")
        if self.published and self.result_digest is None:
            raise ValueError("published 回执必须包含 result_digest")
        if type(self.note) is not str:
            raise ValueError("note 必须是字符串")


def _identity_matches(spec: AttemptSpec, result: AttemptResult) -> bool:
    return (
        result.attempt_id,
        result.fence,
        result.project_id,
        result.kind,
        result.target_commit,
        result.runtime_revision,
    ) == (
        spec.attempt_id,
        spec.fence,
        spec.project_id,
        spec.kind,
        spec.target_commit,
        spec.runtime_revision,
    )


def _canonical_pairs(pairs: tuple[tuple[str, str], ...]) -> str:
    return json.dumps(
        dict(pairs),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _build_record(
    validated: ValidatedAttemptResult,
    result_digest: str,
) -> BuildRecord:
    result = validated.result
    commits = dict(result.input_commits)
    timing = dict(result.timing)
    return BuildRecord(
        project_id=result.project_id,
        kind=result.kind,
        git_commit=commits.get("main"),
        target_commit=result.target_commit,
        repo_commits_json=_canonical_pairs(result.input_commits),
        attempt_id=result.attempt_id,
        runtime_revision=result.runtime_revision,
        input_trees_json=_canonical_pairs(result.input_trees),
        proof_json=result.proof.text,
        log_ref=result.log_ref,
        result_digest=result_digest,
        process_rc=validated.process_rc,
        validated_at=validated.validated_at,
        validation_evidence=validated.evidence.value,
        started_at=timing.get("started_at"),
        finished_at=timing.get("finished_at"),
        status="ok" if result.outcome is AttemptOutcome.SUCCEEDED else "failed",
        note=result.note,
    )


def _record_exposes_secret(
    record: BuildRecord,
    *,
    fence: str,
    claim_token: str,
) -> bool:
    secrets = (fence, claim_token)
    spellings = tuple(
        spelling
        for secret in secrets
        for spelling in (secret, json.dumps(secret, ensure_ascii=True)[1:-1])
    )
    for field in fields(record):
        if field.name == "result_digest":
            continue
        value = getattr(record, field.name)
        if type(value) is str and any(spelling in value for spelling in spellings):
            return True
    return False


class ResultPublisher:
    """发布边界只做结果映射；所有比较与写入由 manifest 单事务完成。"""

    def __init__(
        self,
        manifest_path: Path,
        *,
        busy_timeout_sec: float = DEFAULT_MANIFEST_BUSY_TIMEOUT_SEC,
    ) -> None:
        self._manifest_path = Path(manifest_path)
        self._busy_timeout_sec = validate_manifest_busy_timeout(busy_timeout_sec)

    def publish(self, validated: ValidatedAttemptResult) -> PublishReceipt:
        if type(validated) is not ValidatedAttemptResult:
            raise TypeError("ResultPublisher 只接受 ValidatedAttemptResult")
        result = validated.result
        try:
            result_digest = attempt_result_digest(result)
        except MemoryError:
            raise
        except Exception:
            attempt_id = getattr(result, "attempt_id", "invalid-attempt")
            if type(attempt_id) is not str or not attempt_id.strip():
                attempt_id = "invalid-attempt"
            return PublishReceipt(False, attempt_id, None, "结果无法计算规范摘要")
        if not _identity_matches(validated.spec, result):
            return PublishReceipt(
                False,
                result.attempt_id,
                result_digest,
                "spec 与 result 身份不一致",
            )
        if result.outcome not in _TERMINAL_OUTCOMES:
            return PublishReceipt(
                False,
                result.attempt_id,
                result_digest,
                "结果不是可发布终态，不发布清单",
            )
        try:
            record = _build_record(validated, result_digest)
        except MemoryError:
            raise
        except Exception:
            return PublishReceipt(
                False,
                result.attempt_id,
                result_digest,
                "结果无法规范化为发布记录",
            )
        if _record_exposes_secret(
            record,
            fence=validated.spec.fence,
            claim_token=validated.claim_token,
        ):
            return PublishReceipt(
                False,
                result.attempt_id,
                result_digest,
                "结果包含不可发布的敏感内容",
            )
        try:
            outcome = publish_build(
                record,
                path=self._manifest_path,
                busy_timeout_sec=self._busy_timeout_sec,
            )
        except MemoryError:
            raise
        except Exception:
            return PublishReceipt(False, result.attempt_id, result_digest, "清单写入失败")
        if outcome is ManifestPublishOutcome.CONFLICT:
            return PublishReceipt(
                False,
                result.attempt_id,
                result_digest,
                "同一 attempt 的发布内容冲突",
            )
        if outcome is ManifestPublishOutcome.IDEMPOTENT:
            return PublishReceipt(
                True,
                result.attempt_id,
                result_digest,
                "同一 attempt 已幂等发布",
            )
        if outcome is ManifestPublishOutcome.PUBLISHED:
            return PublishReceipt(True, result.attempt_id, result_digest, "清单发布成功")
        return PublishReceipt(
            False,
            result.attempt_id,
            result_digest,
            "清单返回未知发布结果",
        )


__all__ = ["PublishReceipt", "ResultPublisher"]
