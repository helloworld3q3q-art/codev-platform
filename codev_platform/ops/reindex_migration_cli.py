"""legacy 队列迁移命令的输入边界、停机编排与脱敏呈现。"""
from __future__ import annotations

import json
import os
import stat
import sys
from collections.abc import Callable
from pathlib import Path

from codev_platform.ops.reindex_migration import (
    LegacyMigrationItem,
    PendingMigrationInstruction,
    QueueMigrationWindowReport,
    parse_pending_migration_mapping,
    run_queue_migration_window,
)
from codev_platform.reindex.queue_backend_binding import queue_backend_binding
from codev_platform.reindex.queue_ports import validate_job_identity
from codev_platform.reindex.runtime_owner import (
    QueueOwnerBootstrapRequired,
    QueueOwnerStore,
)

_MIGRATION_TIMEOUT_SEC = 0.3
_MAX_PENDING_MAP_BYTES = 1024 * 1024

Output = Callable[[str], None]
OwnerContextLoader = Callable[[object, dict], tuple[str | None, bool]]
WindowRunner = Callable[..., QueueMigrationWindowReport]
ConfirmedRunner = Callable[..., QueueMigrationWindowReport]


def cmd_migrate_legacy(
    args: object,
    *,
    out: Output = print,
    err: Output | None = None,
    config_loader: Callable[[], dict] | None = None,
    queue_opener: Callable[..., object] | None = None,
    owner_context_loader: OwnerContextLoader | None = None,
    migration_runner: WindowRunner | None = None,
    confirmed_runner: ConfirmedRunner | None = None,
) -> int:
    """执行 legacy 迁移演练或经确认的停机窗口迁移。"""
    write_error = _default_error_output if err is None else err
    if getattr(args, "action", None) != "migrate-legacy":
        write_error("FATAL: 不支持的 legacy 队列迁移动作")
        return 1
    load = _default_config_loader if config_loader is None else config_loader
    open_queue = _default_strict_queue_opener if queue_opener is None else queue_opener
    load_owner = _default_owner_context_loader if owner_context_loader is None else owner_context_loader
    dry_run = run_queue_migration_window if migration_runner is None else migration_runner
    confirmed = run_queue_migration_window if confirmed_runner is None else confirmed_runner
    try:
        pending_mapping = _load_optional_mapping(getattr(args, "pending_map", None))
        load_inputs = _migration_input_loader(
            config_loader=load,
            queue_opener=open_queue,
            owner_context_loader=load_owner,
            pending_mapping=pending_mapping,
        )
        if getattr(args, "yes", False) is True:
            from codev_platform.ops.reindex_admin import (
                run_confirmed_queue_migration_from_loader,
            )

            report = run_confirmed_queue_migration_from_loader(
                input_loader=load_inputs,
                migration_runner=confirmed,
            )
        else:
            report = dry_run(
                **load_inputs(),
                confirmed=False,
                run_lock_acquired=False,
                legacy_worker_stopped=False,
            )
        if type(report) is not QueueMigrationWindowReport:
            raise ValueError("组合迁移报告类型无效")
    except MemoryError:
        raise
    except Exception as error:
        write_error(f"FATAL: legacy 队列迁移失败: {type(error).__name__}")
        return 1
    _render_report(
        report,
        out,
        include_mapping_template=getattr(args, "yes", False) is not True,
    )
    if report.completed is True:
        out("迁移完成：legacy 队列遗留已清空")
        return 0
    out("迁移未完成：禁止启动隔离 worker，请修正遗留后重试")
    return 1


def _migration_input_loader(
    *,
    config_loader: Callable[[], dict],
    queue_opener: Callable[..., object],
    owner_context_loader: OwnerContextLoader,
    pending_mapping: tuple[PendingMigrationInstruction, ...] | None,
) -> Callable[[], dict[str, object]]:
    """延迟队列构造，确保确认迁移只在维护互斥与运行锁内建表或建目录。"""
    def _load() -> dict[str, object]:
        cfg = config_loader()
        if type(cfg) is not dict:
            raise ValueError("配置根类型无效")
        queue = queue_opener(fail_soft=False)
        stable_owner_token, bootstrap = owner_context_loader(queue, cfg)
        _validate_owner_context(stable_owner_token, bootstrap)
        return {
            "queue": queue,
            "pending_mapping": pending_mapping,
            "stable_owner_token": stable_owner_token,
            "bootstrap": bootstrap,
            "timeout_sec": _MIGRATION_TIMEOUT_SEC,
        }

    return _load


def _load_optional_mapping(value: object) -> tuple[PendingMigrationInstruction, ...] | None:
    if value is None:
        return None
    raw = _read_bounded_regular_file(value)
    return parse_pending_migration_mapping(raw)


def _read_bounded_regular_file(value: object) -> bytes:
    """只读取不跟随符号链接的普通小文件，避免映射输入扩大运维攻击面。"""
    if type(value) is not str or not value.strip():
        raise ValueError("pending 映射路径无效")
    path = Path(value)
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ValueError("pending 映射文件不可读取") from error
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_PENDING_MAP_BYTES:
        raise ValueError("pending 映射必须是受限普通文件")
    try:
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if not _same_regular_file(metadata, opened):
                raise ValueError("pending 映射文件在读取期间发生变化")
            raw = stream.read(_MAX_PENDING_MAP_BYTES + 1)
    except OSError:
        raise ValueError("pending 映射文件不可读取") from None
    if len(raw) > _MAX_PENDING_MAP_BYTES:
        raise ValueError("pending 映射文件过大")
    return raw


def _same_regular_file(before: object, opened: object) -> bool:
    """拒绝 lstat 与已打开描述符不是同一普通文件的竞态替换。"""
    return bool(
        stat.S_ISREG(getattr(opened, "st_mode", 0))
        and getattr(opened, "st_size", _MAX_PENDING_MAP_BYTES + 1) <= _MAX_PENDING_MAP_BYTES
        and getattr(before, "st_dev", None) == getattr(opened, "st_dev", None)
        and getattr(before, "st_ino", None) == getattr(opened, "st_ino", None)
    )


def _validate_owner_context(stable_owner_token: object, bootstrap: object) -> None:
    if stable_owner_token is not None and (
        type(stable_owner_token) is not str or not stable_owner_token
    ):
        raise ValueError("稳定 owner 上下文无效")
    if type(bootstrap) is not bool:
        raise ValueError("稳定 owner 引导状态无效")
    if stable_owner_token is not None and bootstrap:
        raise ValueError("稳定 owner 与引导状态冲突")


def _render_report(
    report: QueueMigrationWindowReport,
    out: Output,
    *,
    include_mapping_template: bool,
) -> None:
    """仅呈现操作员修复所需字段，永不输出 owner token。"""
    out(
        "迁移审计："
        f"active_已执行={_yes_no(report.active.executed)} "
        f"pending_已执行={_yes_no(report.pending.executed)} "
        f"完成={_yes_no(report.completed)}"
    )
    for item in report.active.remaining:
        out(_render_legacy_item(item))
    if report.pending.remaining_count:
        out(f"迁移遗留：pending 数量={report.pending.remaining_count}")
    if include_mapping_template:
        _render_mapping_template(report.active.remaining, out)


def _render_legacy_item(item: LegacyMigrationItem) -> str:
    reasons = ",".join(_display_text(reason) for reason in item.reasons)
    return (
        "迁移遗留："
        f"阶段={_display_text(item.phase)} "
        f"key={_display_text(item.key)} "
        f"动作={_display_text(item.action)} "
        f"原因={reasons or 'unknown'}"
    )


def _render_mapping_template(items: tuple[LegacyMigrationItem, ...], out: Output) -> None:
    mappings: list[dict[str, str]] = []
    for item in items:
        if item.phase != "pending" or type(item.pending_version) is not str:
            continue
        identity = _split_legacy_key(item.key)
        if identity is None:
            continue
        project_id, kind = identity
        mappings.append(
            {
                "project_id": project_id,
                "kind": kind,
                "pending_version": item.pending_version,
                "target_commit": "<请填写完整小写OID>",
            }
        )
    if mappings:
        template = json.dumps(
            {"schema_version": 1, "items": mappings},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        out(f"待补 pending 映射模板：{template}")


def _split_legacy_key(value: object) -> tuple[str, str] | None:
    if type(value) is not str:
        return None
    project_id, separator, kind = value.rpartition("__")
    if not separator:
        return None
    try:
        return validate_job_identity(project_id, kind)
    except (TypeError, ValueError):
        return None


def _display_text(value: object) -> str:
    if type(value) is not str or len(value) > 256:
        return "invalid"
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return "invalid"
    return value


def _yes_no(value: object) -> str:
    return "是" if value is True else "否"


def _default_owner_context_loader(queue: object, cfg: dict) -> tuple[str | None, bool]:
    binding = queue_backend_binding(queue, cfg)
    try:
        owner = QueueOwnerStore().load(binding)
    except QueueOwnerBootstrapRequired:
        return None, True
    return owner.token, False


def _default_config_loader() -> dict:
    from codev_platform.core.config import load_config

    return load_config()


def _default_strict_queue_opener(*, fail_soft: bool) -> object:
    from codev_platform.reindex import open_default_queue

    if fail_soft is not False:
        raise ValueError("运维队列必须严格打开")
    return open_default_queue(fail_soft=False)


def _default_error_output(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


__all__ = ["cmd_migrate_legacy"]
