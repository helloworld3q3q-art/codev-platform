"""legacy reindex 队列的严格映射、受控迁移与写后审计。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from codev_platform.reindex.queue_migration import (
    LegacyActiveResolution,
    detect_legacy_queue_entries,
    dispose_expired_legacy_active,
    plan_expired_legacy_active,
)
from codev_platform.reindex.queue_ports import (
    Job,
    JobMeta,
    PendingMigrationOutcome,
    PendingMigrationResult,
    QueueSnapshot,
    validate_job_identity,
    validate_target_commit,
    validate_timeout,
)


class LegacyMigrationPreconditionError(RuntimeError):
    """运维迁移缺少可证明的安全前置条件。"""


class PendingMigrationMappingError(ValueError):
    """pending 映射不完整、歧义或不满足精确 CAS 前置条件。"""


class _SnapshotQueue(Protocol):
    def snapshot(self) -> QueueSnapshot: ...


@dataclass(frozen=True, slots=True)
class PendingMigrationInstruction:
    """一个不可从 HEAD 推导、只允许精确 OID 的 pending 更新指令。"""

    project_id: str
    kind: str
    pending_version: str = field(repr=False)
    target_commit: str

    def __post_init__(self) -> None:
        try:
            validate_job_identity(self.project_id, self.kind)
            validate_target_commit(self.target_commit)
        except (TypeError, ValueError):
            raise PendingMigrationMappingError("pending 映射身份或目标版本无效") from None
        if (
            type(self.pending_version) is not str
            or not self.pending_version.strip()
            or len(self.pending_version) > 512
            or any(ord(char) < 32 or ord(char) == 127 for char in self.pending_version)
        ):
            raise PendingMigrationMappingError("pending 映射版本能力无效")

    @property
    def key(self) -> tuple[str, str]:
        return self.project_id, self.kind


@dataclass(frozen=True, slots=True)
class LegacyMigrationItem:
    """面向 CLI 的脱敏 legacy 项，不携带 owner token。"""

    phase: str
    key: str
    reasons: tuple[str, ...]
    action: str
    pending_version: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class LegacyMigrationReport:
    """一次 active dry-run 或确认执行后的结构化审计报告。"""

    executed: bool
    items: tuple[LegacyMigrationItem, ...]
    pending_unmigrated_count: int
    completed: bool
    remaining: tuple[LegacyMigrationItem, ...] = ()
    legacy_worker_stopped: bool = False


@dataclass(frozen=True, slots=True)
class PendingMigrationItem:
    """单条 pending CAS 迁移计划或结果，版本能力不参与 repr。"""

    key: str
    action: str
    pending_version: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class PendingMigrationReport:
    """pending 映射执行报告；completed 只取决于写后新快照。"""

    executed: bool
    items: tuple[PendingMigrationItem, ...]
    completed: bool
    remaining_count: int
    legacy_worker_stopped: bool = False


@dataclass(frozen=True, slots=True)
class QueueMigrationWindowReport:
    """同一停机/锁窗口中的 active 与 pending 组合迁移报告。"""

    active: LegacyMigrationReport
    pending: PendingMigrationReport
    completed: bool
    legacy_worker_stopped: bool


@dataclass(frozen=True, slots=True)
class _PendingPlan:
    matches: tuple[tuple[Job, PendingMigrationInstruction], ...]
    items: tuple[PendingMigrationItem, ...]
    ready: bool


def _read_snapshot(queue: _SnapshotQueue) -> QueueSnapshot:
    try:
        snapshot = queue.snapshot()
    except Exception:  # noqa: BLE001 - 后端异常可能携带 DSN 或 owner
        raise LegacyMigrationPreconditionError("无法读取 legacy queue 快照") from None
    if type(snapshot) is not QueueSnapshot:
        raise LegacyMigrationPreconditionError("legacy queue 快照类型无效")
    return snapshot


def _strict_object(pairs: list[tuple[object, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise PendingMigrationMappingError("pending 映射 JSON 含重复或无效字段")
        result[key] = value
    return result


def _require_keys(value: object, expected: set[str], label: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != expected:
        raise PendingMigrationMappingError(f"pending 映射 {label} 字段集合无效")
    return value


def _decode_mapping(raw: bytes | str) -> object:
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8", "strict")
        except UnicodeDecodeError:
            raise PendingMigrationMappingError("pending 映射必须是 UTF-8 JSON") from None
    elif type(raw) is str:
        text = raw
    else:
        raise PendingMigrationMappingError("pending 映射必须是 UTF-8 bytes 或字符串")
    if not text or len(text.encode("utf-8")) > 1024 * 1024:
        raise PendingMigrationMappingError("pending 映射大小无效")
    try:
        return json.loads(text, object_pairs_hook=_strict_object)
    except PendingMigrationMappingError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError):
        raise PendingMigrationMappingError("pending 映射不是严格 JSON 对象") from None


def parse_pending_migration_mapping(raw: bytes | str) -> tuple[PendingMigrationInstruction, ...]:
    """解析无重复键、无额外字段、仅含显式完整 OID 的 JSON 映射。"""
    root = _require_keys(_decode_mapping(raw), {"schema_version", "items"}, "根对象")
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise PendingMigrationMappingError("pending 映射 schema_version 无效")
    if type(root["items"]) is not list:
        raise PendingMigrationMappingError("pending 映射 items 必须是数组")
    instructions: list[PendingMigrationInstruction] = []
    seen: set[tuple[str, str]] = set()
    for value in root["items"]:
        item = _require_keys(
            value,
            {"project_id", "kind", "pending_version", "target_commit"},
            "条目",
        )
        instruction = PendingMigrationInstruction(
            item["project_id"],  # type: ignore[arg-type]
            item["kind"],  # type: ignore[arg-type]
            item["pending_version"],  # type: ignore[arg-type]
            item["target_commit"],  # type: ignore[arg-type]
        )
        if instruction.key in seen:
            raise PendingMigrationMappingError("pending 映射含重复 project_id/kind")
        seen.add(instruction.key)
        instructions.append(instruction)
    return tuple(instructions)


def _pending_jobs(snapshot: QueueSnapshot) -> dict[tuple[str, str], Job]:
    jobs: dict[tuple[str, str], Job] = {}
    for entry in detect_legacy_queue_entries(snapshot):
        if entry.phase != "pending":
            continue
        identity = entry.job.project_id, entry.job.kind
        if identity in jobs or not entry.job.pending_version:
            raise PendingMigrationMappingError("legacy pending 缺少唯一 CAS 版本")
        jobs[identity] = entry.job
    return jobs


def _pending_plan(
    snapshot: QueueSnapshot,
    mapping: tuple[PendingMigrationInstruction, ...],
) -> _PendingPlan:
    jobs = _pending_jobs(snapshot)
    if type(mapping) is not tuple or any(type(item) is not PendingMigrationInstruction for item in mapping):
        raise PendingMigrationMappingError("pending 映射类型无效")
    instructions: dict[tuple[str, str], PendingMigrationInstruction] = {}
    for item in mapping:
        if item.key in instructions:
            raise PendingMigrationMappingError("pending 映射含重复 project_id/kind")
        instructions[item.key] = item
    if set(jobs) != set(instructions):
        raise PendingMigrationMappingError("pending 映射必须与 legacy pending 快照一一对应")
    matches: list[tuple[Job, PendingMigrationInstruction]] = []
    items: list[PendingMigrationItem] = []
    ready = True
    for identity, job in jobs.items():
        instruction = instructions[identity]
        if instruction.pending_version != job.pending_version:
            items.append(PendingMigrationItem(job.key, "version_conflict", job.pending_version))
            ready = False
            continue
        matches.append((job, instruction))
        items.append(PendingMigrationItem(job.key, "planned", job.pending_version))
    return _PendingPlan(tuple(matches), tuple(items), ready)


def _active_items(
    snapshot: QueueSnapshot,
    *,
    stable_owner_token: str | None,
    bootstrap: bool,
    resolutions: tuple[LegacyActiveResolution, ...] = (),
) -> tuple[LegacyMigrationItem, ...]:
    actions = {
        (item.phase, item.key): item.action.value
        for item in plan_expired_legacy_active(
            snapshot,
            stable_owner_token=stable_owner_token,
            bootstrap=bootstrap,
        )
    }
    actions.update({(item.phase, item.key): item.action.value for item in resolutions})
    grouped: dict[tuple[str, str], tuple[list[str], str | None]] = {}
    for entry in detect_legacy_queue_entries(snapshot, stable_owner_token=stable_owner_token):
        marker = entry.phase, entry.job.key
        reasons, pending_version = grouped.setdefault(marker, ([], None))
        reasons.append(entry.reason.value)
        if entry.phase == "pending":
            grouped[marker] = reasons, entry.job.pending_version
    items: list[LegacyMigrationItem] = []
    for (phase, key), (reasons, pending_version) in grouped.items():
        if phase == "pending":
            action = "pending_mapping_required"
        elif phase == "active":
            action = "blocked_not_expired"
        else:
            action = actions.get((phase, key), "manual_recovery_required")
        items.append(LegacyMigrationItem(phase, key, tuple(reasons), action, pending_version))
    return tuple(items)


def _active_report(
    snapshot: QueueSnapshot,
    *,
    executed: bool,
    stable_owner_token: str | None,
    bootstrap: bool,
    legacy_worker_stopped: bool,
    resolutions: tuple[LegacyActiveResolution, ...] = (),
    remaining: tuple[LegacyMigrationItem, ...] | None = None,
) -> LegacyMigrationReport:
    items = _active_items(
        snapshot,
        stable_owner_token=stable_owner_token,
        bootstrap=bootstrap,
        resolutions=resolutions,
    )
    residual = items if remaining is None else remaining
    pending_count = sum(item.phase == "pending" for item in residual)
    return LegacyMigrationReport(
        executed,
        items,
        pending_count,
        not residual,
        residual,
        legacy_worker_stopped,
    )


def _pending_report(
    plan: _PendingPlan,
    *,
    executed: bool,
    legacy_worker_stopped: bool,
    outcomes: tuple[PendingMigrationItem, ...] | None = None,
    remaining_count: int | None = None,
) -> PendingMigrationReport:
    items = plan.items if outcomes is None else outcomes
    residual = len(plan.items) if remaining_count is None else remaining_count
    return PendingMigrationReport(executed, items, residual == 0, residual, legacy_worker_stopped)


def _require_write_window(
    *,
    run_lock_acquired: bool,
    legacy_worker_stopped: bool,
    active_items: tuple[LegacyMigrationItem, ...] = (),
) -> None:
    if run_lock_acquired is not True:
        raise LegacyMigrationPreconditionError("未持有 reindex run lock，拒绝迁移")
    if legacy_worker_stopped is not True:
        raise LegacyMigrationPreconditionError("未取得旧 worker 停止证明，拒绝迁移")
    if any(item.action == "blocked_not_expired" for item in active_items):
        raise LegacyMigrationPreconditionError("存在未过期 legacy active，拒绝部分迁移")


def _migrate_pending(
    queue: object,
    matches: tuple[tuple[Job, PendingMigrationInstruction], ...],
    *,
    timeout_sec: float,
) -> tuple[PendingMigrationItem, ...]:
    if not matches:
        return ()
    migrate = getattr(queue, "migrate_pending", None)
    if not callable(migrate):
        raise LegacyMigrationPreconditionError("队列不支持 pending 精确迁移")
    outcomes: list[PendingMigrationItem] = []
    for expected, instruction in matches:
        try:
            result = migrate(
                expected,
                JobMeta(target_commit=instruction.target_commit),
                timeout_sec=timeout_sec,
            )
        except Exception:  # noqa: BLE001 - 后端异常可能携带 DSN 或 owner
            raise LegacyMigrationPreconditionError("pending 精确迁移失败") from None
        if type(result) is not PendingMigrationResult:
            raise LegacyMigrationPreconditionError("pending 迁移后端返回无效结果")
        outcomes.append(PendingMigrationItem(expected.key, result.outcome.value, expected.pending_version or ""))
    return tuple(outcomes)


def _all_pending_migrated(outcomes: tuple[PendingMigrationItem, ...]) -> bool:
    return all(item.action == PendingMigrationOutcome.MIGRATED.value for item in outcomes)


def _remaining_pending_count(snapshot: QueueSnapshot) -> int:
    return len(_pending_jobs(snapshot))


def run_pending_queue_migration(
    queue: object,
    mapping: tuple[PendingMigrationInstruction, ...],
    *,
    confirmed: bool,
    run_lock_acquired: bool,
    legacy_worker_stopped: bool,
    timeout_sec: float,
) -> PendingMigrationReport:
    """单独执行 pending CAS；确认写入只接受独立旧 worker 停止证明。"""
    if type(confirmed) is not bool:
        raise ValueError("confirmed 必须是布尔值")
    timeout = validate_timeout(timeout_sec)
    snapshot = _read_snapshot(queue)
    plan = _pending_plan(snapshot, mapping)
    dry_run = _pending_report(plan, executed=False, legacy_worker_stopped=legacy_worker_stopped)
    if not confirmed or not plan.ready:
        return dry_run
    _require_write_window(
        run_lock_acquired=run_lock_acquired,
        legacy_worker_stopped=legacy_worker_stopped,
    )
    outcomes = _migrate_pending(queue, plan.matches, timeout_sec=timeout)
    fresh = _read_snapshot(queue)
    return _pending_report(
        plan,
        executed=True,
        legacy_worker_stopped=legacy_worker_stopped,
        outcomes=outcomes,
        remaining_count=_remaining_pending_count(fresh),
    )


def run_legacy_queue_migration(
    queue: object,
    *,
    confirmed: bool,
    run_lock_acquired: bool,
    legacy_worker_stopped: bool,
    stable_owner_token: str | None = None,
    bootstrap: bool = False,
    timeout_sec: float,
) -> LegacyMigrationReport:
    """只收口 expired active；确认执行前拒绝留下未映射 pending。"""
    if type(confirmed) is not bool:
        raise ValueError("confirmed 必须是布尔值")
    timeout = validate_timeout(timeout_sec)
    snapshot = _read_snapshot(queue)
    dry_run = _active_report(
        snapshot,
        executed=False,
        stable_owner_token=stable_owner_token,
        bootstrap=bootstrap,
        legacy_worker_stopped=legacy_worker_stopped,
    )
    if not confirmed:
        return dry_run
    if _pending_jobs(snapshot):
        raise PendingMigrationMappingError("存在 legacy pending，必须提供完整映射后组合迁移")
    _require_write_window(
        run_lock_acquired=run_lock_acquired,
        legacy_worker_stopped=legacy_worker_stopped,
        active_items=dry_run.items,
    )
    try:
        resolutions = dispose_expired_legacy_active(
            queue,
            snapshot,
            stable_owner_token=stable_owner_token,
            bootstrap=bootstrap,
            timeout_sec=timeout,
        )
    except Exception:  # noqa: BLE001 - 后端异常可能含 DSN、owner 等敏感运行细节
        raise LegacyMigrationPreconditionError("legacy active 受控处置失败") from None
    fresh = _read_snapshot(queue)
    remaining = _active_items(
        fresh,
        stable_owner_token=stable_owner_token,
        bootstrap=bootstrap,
    )
    return _active_report(
        snapshot,
        executed=True,
        stable_owner_token=stable_owner_token,
        bootstrap=bootstrap,
        legacy_worker_stopped=legacy_worker_stopped,
        resolutions=resolutions,
        remaining=remaining,
    )


def run_queue_migration_window(
    queue: object,
    *,
    pending_mapping: tuple[PendingMigrationInstruction, ...] | None,
    confirmed: bool,
    run_lock_acquired: bool,
    legacy_worker_stopped: bool,
    stable_owner_token: str | None = None,
    bootstrap: bool = False,
    timeout_sec: float,
) -> QueueMigrationWindowReport:
    """在调用方已持有的同一停机/锁窗口中组合迁移，并只做一次写后快照。"""
    if type(confirmed) is not bool:
        raise ValueError("confirmed 必须是布尔值")
    timeout = validate_timeout(timeout_sec)
    snapshot = _read_snapshot(queue)
    active_dry = _active_report(
        snapshot,
        executed=False,
        stable_owner_token=stable_owner_token,
        bootstrap=bootstrap,
        legacy_worker_stopped=legacy_worker_stopped,
    )
    pending_plan = (
        _pending_plan(snapshot, pending_mapping)
        if pending_mapping is not None
        else _PendingPlan((), (), True)
    )
    pending_dry = _pending_report(
        pending_plan,
        executed=False,
        legacy_worker_stopped=legacy_worker_stopped,
    )
    dry_result = QueueMigrationWindowReport(
        active_dry,
        pending_dry,
        active_dry.completed and pending_dry.completed,
        legacy_worker_stopped,
    )
    if not confirmed:
        return dry_result
    if _pending_jobs(snapshot) and pending_mapping is None:
        raise PendingMigrationMappingError("存在 legacy pending，必须提供完整映射后组合迁移")
    if not pending_plan.ready:
        return dry_result
    _require_write_window(
        run_lock_acquired=run_lock_acquired,
        legacy_worker_stopped=legacy_worker_stopped,
        active_items=active_dry.items,
    )
    outcomes = _migrate_pending(queue, pending_plan.matches, timeout_sec=timeout)
    after_pending = _read_snapshot(queue) if pending_plan.matches else snapshot
    remaining_pending = _remaining_pending_count(after_pending)
    if not _all_pending_migrated(outcomes) or remaining_pending:
        active_remaining = _active_items(
            after_pending,
            stable_owner_token=stable_owner_token,
            bootstrap=bootstrap,
        )
        active = _active_report(
            after_pending,
            executed=False,
            stable_owner_token=stable_owner_token,
            bootstrap=bootstrap,
            legacy_worker_stopped=legacy_worker_stopped,
            remaining=active_remaining,
        )
        pending = _pending_report(
            pending_plan,
            executed=True,
            legacy_worker_stopped=legacy_worker_stopped,
            outcomes=outcomes,
            remaining_count=remaining_pending,
        )
        return QueueMigrationWindowReport(active, pending, False, legacy_worker_stopped)
    try:
        resolutions = dispose_expired_legacy_active(
            queue,
            after_pending,
            stable_owner_token=stable_owner_token,
            bootstrap=bootstrap,
            timeout_sec=timeout,
        )
    except Exception:  # noqa: BLE001 - 后端异常可能含 DSN、owner 等敏感运行细节
        raise LegacyMigrationPreconditionError("legacy active 受控处置失败") from None
    fresh = _read_snapshot(queue)
    active_remaining = _active_items(
        fresh,
        stable_owner_token=stable_owner_token,
        bootstrap=bootstrap,
    )
    active = _active_report(
        after_pending,
        executed=True,
        stable_owner_token=stable_owner_token,
        bootstrap=bootstrap,
        legacy_worker_stopped=legacy_worker_stopped,
        resolutions=resolutions,
        remaining=active_remaining,
    )
    pending = _pending_report(
        pending_plan,
        executed=True,
        legacy_worker_stopped=legacy_worker_stopped,
        outcomes=outcomes,
        remaining_count=_remaining_pending_count(fresh),
    )
    return QueueMigrationWindowReport(
        active,
        pending,
        active.completed and pending.completed,
        legacy_worker_stopped,
    )


__all__ = [
    "LegacyMigrationItem",
    "LegacyMigrationPreconditionError",
    "LegacyMigrationReport",
    "PendingMigrationInstruction",
    "PendingMigrationItem",
    "PendingMigrationMappingError",
    "PendingMigrationReport",
    "QueueMigrationWindowReport",
    "parse_pending_migration_mapping",
    "run_legacy_queue_migration",
    "run_pending_queue_migration",
    "run_queue_migration_window",
]
