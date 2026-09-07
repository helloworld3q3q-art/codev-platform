"""codev-reindex 专用 systemd 维护窗口，不触及其他常驻服务。"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from codev_platform.ops.reindex_maintenance_provision import (
    provision_reindex_maintenance,
)
from codev_platform.ops.reindex_maintenance_cli import cmd_reindex_maintenance, register
from codev_platform.ops import reindex_maintenance_systemd as _systemd

# 保持既有调用方和测试的维护模块入口稳定；systemd 细节由独立叶子模块负责。
CommandRunner = _systemd.CommandRunner
ReindexMaintenanceError = _systemd.ReindexMaintenanceError
StabilityProbe = _systemd.StabilityProbe
_REINDEX_UNIT = _systemd._REINDEX_UNIT
_SYSTEMCTL_TIMEOUT_SEC = _systemd._SYSTEMCTL_TIMEOUT_SEC
_default_command_runner = _systemd._default_command_runner
_default_stability_probe = _systemd._default_stability_probe
_read_restart_count = _systemd._read_restart_count
_read_systemctl_properties = _systemd._read_systemctl_properties
_require_baseline_restart = _systemd._require_baseline_restart
_run_systemctl = _systemd._run_systemctl
_stop_reindex_safely = _systemd._stop_reindex_safely
_wait_for_stable_worker = _systemd._wait_for_stable_worker
time = _systemd.time

_DROPIN_NAME = "10-codev-reindex-maintenance.conf"
_DROPIN_CONTENT = "[Service]\nRestart=no\n"
_DROPIN_PATH = Path("/etc/systemd/system") / f"{_REINDEX_UNIT}.d" / _DROPIN_NAME
_MAX_DROPIN_BYTES = 128

StopProof = Callable[[], None]
ExternalWorkerProof = Callable[[], None]
GateActivator = Callable[[], None]
GateDeactivator = Callable[[], None]
GateActiveReader = Callable[[], bool]
GateRecordReader = Callable[[], object]
Output = Callable[[str], None]


def prepare_reindex_maintenance(
    *,
    platform_name: str | None = None,
    dropin_path: Path = _DROPIN_PATH,
    command_runner: CommandRunner | None = None,
    stop_proof: StopProof | None = None,
    external_worker_proof: ExternalWorkerProof | None = None,
    gate_activator: GateActivator | None = None,
    codegraph_prepare: Callable[[], None] | None = None,
    codegraph_maintenance_proof: Callable[[], None] | None = None,
    webhook_prepare: Callable[[], None] | None = None,
    webhook_maintenance_proof: Callable[[], None] | None = None,
) -> None:
    """委托 prepare 叶子，先耐久发布 marker，再收敛两个服务边界。"""
    from codev_platform.ops.reindex_maintenance_prepare import (
        prepare_reindex_maintenance as prepare,
    )

    prepare(
        maintenance=sys.modules[__name__],
        platform_name=platform_name,
        dropin_path=dropin_path,
        command_runner=command_runner,
        stop_proof=stop_proof,
        external_worker_proof=external_worker_proof,
        gate_activator=gate_activator,
        codegraph_prepare=(
            _default_codegraph_prepare if codegraph_prepare is None else codegraph_prepare
        ),
        codegraph_maintenance_proof=(
            _default_codegraph_maintenance_proof
            if codegraph_maintenance_proof is None
            else codegraph_maintenance_proof
        ),
        webhook_prepare=(
            (
                lambda: _default_webhook_prepare(
                    platform_name=platform_name,
                    command_runner=command_runner,
                )
            )
            if webhook_prepare is None
            else webhook_prepare
        ),
        webhook_maintenance_proof=(
            (
                lambda: _default_webhook_maintenance_proof(
                    platform_name=platform_name,
                    command_runner=command_runner,
                )
            )
            if webhook_maintenance_proof is None
            else webhook_maintenance_proof
        ),
    )


def restore_reindex_maintenance(
    *,
    platform_name: str | None = None,
    dropin_path: Path = _DROPIN_PATH,
    command_runner: CommandRunner | None = None,
    stop_proof: StopProof | None = None,
    stability_probe: StabilityProbe | None = None,
    external_worker_proof: ExternalWorkerProof | None = None,
    gate_deactivator: GateDeactivator | None = None,
    gate_activator: GateActivator | None = None,
    gate_active_reader: GateActiveReader | None = None,
    standby_armer: Callable[[], object] | None = None,
    unit_invocation_reader: Callable[[], str] | None = None,
    standby_claimer: Callable[[str, str], object] | None = None,
    standby_renewer: Callable[[str, str], object] | None = None,
    standby_completer: Callable[[str, str], None] | None = None,
    codegraph_prepare: Callable[[], None] | None = None,
    codegraph_maintenance_proof: Callable[[], None] | None = None,
    webhook_prepare: Callable[[], None] | None = None,
    webhook_maintenance_proof: Callable[[], None] | None = None,
) -> None:
    """委托待命恢复状态机，确保 marker 最后才交接实际写权限。"""
    from codev_platform.ops.reindex_maintenance_restore import (
        restore_reindex_maintenance as restore,
    )

    restore(
        platform_name=platform_name,
        dropin_path=dropin_path,
        command_runner=command_runner,
        stop_proof=stop_proof,
        stability_probe=stability_probe,
        external_worker_proof=external_worker_proof,
        gate_deactivator=gate_deactivator,
        gate_activator=gate_activator,
        gate_active_reader=gate_active_reader,
        standby_armer=standby_armer,
        unit_invocation_reader=unit_invocation_reader,
        standby_claimer=standby_claimer,
        standby_renewer=standby_renewer,
        standby_completer=standby_completer,
        codegraph_prepare=(
            _default_codegraph_prepare if codegraph_prepare is None else codegraph_prepare
        ),
        codegraph_maintenance_proof=(
            _default_codegraph_maintenance_proof
            if codegraph_maintenance_proof is None
            else codegraph_maintenance_proof
        ),
        webhook_prepare=(
            (
                lambda: _default_webhook_prepare(
                    platform_name=platform_name,
                    command_runner=command_runner,
                )
            )
            if webhook_prepare is None
            else webhook_prepare
        ),
        webhook_maintenance_proof=(
            (
                lambda: _default_webhook_maintenance_proof(
                    platform_name=platform_name,
                    command_runner=command_runner,
                )
            )
            if webhook_maintenance_proof is None
            else webhook_maintenance_proof
        ),
    )


def inspect_reindex_maintenance(
    *,
    platform_name: str | None = None,
    dropin_path: Path = _DROPIN_PATH,
    stop_proof: StopProof | None = None,
    external_worker_proof: ExternalWorkerProof | None = None,
    gate_active_reader: GateActiveReader | None = None,
    gate_record_reader: GateRecordReader | None = None,
    codegraph_maintenance_proof: Callable[[], None] | None = None,
    webhook_maintenance_proof: Callable[[], None] | None = None,
) -> None:
    """只读证明维护 marker、受管 drop-in、外部 worker 与 systemd 均处于安全态。"""
    _require_linux(platform_name)
    path = _validate_dropin_path(dropin_path)
    prove = _default_stop_proof if stop_proof is None else stop_proof
    prove_external = (
        _default_external_worker_proof if external_worker_proof is None else external_worker_proof
    )
    gate_active = _default_gate_active_reader if gate_active_reader is None else gate_active_reader
    read_gate_record = (
        _default_gate_record_reader if gate_record_reader is None else gate_record_reader
    )
    prove_codegraph = (
        _default_codegraph_maintenance_proof
        if codegraph_maintenance_proof is None
        else codegraph_maintenance_proof
    )
    prove_webhook = (
        (lambda: _default_webhook_maintenance_proof(platform_name=platform_name))
        if webhook_maintenance_proof is None
        else webhook_maintenance_proof
    )
    if not all(
        callable(item)
        for item in (
            prove,
            prove_external,
            gate_active,
            read_gate_record,
            prove_codegraph,
            prove_webhook,
        )
    ):
        raise ReindexMaintenanceError("维护窗口检查适配器不可用")
    try:
        _require_owned_dropin(path)
        _require_active_gate(gate_active)
        _require_maintenance_phase(read_gate_record)
        prove_external()
        prove()
        prove_codegraph()
        prove_webhook()
    except MemoryError:
        raise
    except ReindexMaintenanceError:
        raise
    except Exception as error:
        raise ReindexMaintenanceError("reindex 维护窗口状态无法证明") from error


def prove_reindex_maintenance_dropin() -> None:
    """证明 M1 仍保留受管 Restart=no，避免 CodeGraph 恢复期提前放开 worker。"""
    _require_owned_dropin(_DROPIN_PATH)


def _require_linux(platform_name: str | None) -> None:
    platform = sys.platform if platform_name is None else platform_name
    if type(platform) is not str or not platform.startswith("linux"):
        raise ReindexMaintenanceError("当前平台不支持 systemd reindex 维护窗口")


def _validate_dropin_path(value: Path) -> Path:
    path = Path(value)
    if (
        not path.is_absolute()
        or path.name != _DROPIN_NAME
        or path.parent.name != f"{_REINDEX_UNIT}.d"
    ):
        raise ReindexMaintenanceError("reindex 维护 drop-in 路径无效")
    return path


def _write_dropin(path: Path) -> None:
    """生产固定路径走 root 可信 dirfd；自定义测试路径保留可移植实现。"""
    if path == _DROPIN_PATH and sys.platform.startswith("linux"):
        _write_trusted_dropin(path)
    else:
        _write_portable_dropin(path)
    _require_owned_dropin(path)


def _write_trusted_dropin(path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        TrustedManagedPathError,
        write_root_owned_regular_file_atomic,
    )

    try:
        write_root_owned_regular_file_atomic(
            path,
            _DROPIN_CONTENT.encode(),
            mode=0o644,
            uid=0,
            gid=0,
        )
    except TrustedManagedPathError as error:
        raise ReindexMaintenanceError("无法写入受信任的 reindex 维护 drop-in") from error


def _write_portable_dropin(path: Path) -> None:
    """仅供自定义测试路径使用的可移植原子替换。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{_DROPIN_NAME}.",
            suffix=".tmp",
            dir=path.parent,
            text=True,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(_DROPIN_CONTENT)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
    except MemoryError:
        raise
    except OSError as error:
        raise ReindexMaintenanceError("无法写入 reindex 维护 drop-in") from error


def _require_owned_dropin(path: Path) -> None:
    """生产固定路径复用 root 可信 dirfd；自定义测试路径保留可移植证明。"""
    if path == _DROPIN_PATH and sys.platform.startswith("linux"):
        _require_trusted_owned_dropin(path)
        return
    _require_portable_owned_dropin(path)


def _require_trusted_owned_dropin(path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        TrustedManagedPathError,
        read_optional_root_owned_regular_file_snapshot,
    )

    try:
        snapshot = read_optional_root_owned_regular_file_snapshot(
            path,
            max_bytes=_MAX_DROPIN_BYTES,
        )
    except TrustedManagedPathError as error:
        raise ReindexMaintenanceError("reindex 维护 drop-in 不受信任") from error
    if (
        snapshot is None
        or snapshot.content != _DROPIN_CONTENT.encode()
        or snapshot.mode != 0o644
        or snapshot.uid != 0
        or snapshot.gid != 0
    ):
        raise ReindexMaintenanceError("reindex 维护 drop-in 不受信任")


def _require_portable_owned_dropin(path: Path) -> None:
    """仅供非 Linux 测试路径使用；仍拒绝链接、替换和非精确内容。"""
    try:
        initial = path.lstat()
        if not stat.S_ISREG(initial.st_mode) or initial.st_size > _MAX_DROPIN_BYTES:
            raise ReindexMaintenanceError("未找到受管 reindex 维护 drop-in")
        with path.open("rb") as stream:
            current = os.fstat(stream.fileno())
            if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (
                initial.st_dev,
                initial.st_ino,
            ):
                raise ReindexMaintenanceError("reindex 维护 drop-in 已变化")
            content = stream.read(_MAX_DROPIN_BYTES + 1)
    except ReindexMaintenanceError:
        raise
    except OSError as error:
        raise ReindexMaintenanceError("未找到受管 reindex 维护 drop-in") from error
    try:
        normalized = content.decode("utf-8", "strict").replace("\r\n", "\n")
    except UnicodeDecodeError:
        normalized = ""
    if normalized != _DROPIN_CONTENT:
        raise ReindexMaintenanceError("reindex 维护 drop-in 内容不受信任")


def _require_active_gate(gate_active: GateActiveReader) -> None:
    """确认维护门禁仍处于启用状态，任何无法确认都拒绝继续恢复。"""
    try:
        if gate_active() is not True:
            raise ReindexMaintenanceError("reindex 维护门禁未启用")
    except MemoryError:
        raise
    except ReindexMaintenanceError:
        raise
    except Exception as error:
        raise ReindexMaintenanceError("reindex 维护门禁状态无法证明") from error


def _require_maintenance_phase(read_record: GateRecordReader) -> None:
    """管理员写操作只接受 maintenance，restore 任一阶段都只能待命。"""
    try:
        record = read_record()
        if getattr(record, "phase", None) != "maintenance":
            raise ReindexMaintenanceError("reindex 维护窗口不处于维护稳态")
    except MemoryError:
        raise
    except ReindexMaintenanceError:
        raise
    except Exception as error:
        raise ReindexMaintenanceError("reindex 维护 marker 状态无法证明") from error


def _attempt_compensation_action(action: Callable[[], None]) -> bool:
    """补偿必须尽力完成全部动作；普通异常只降低安全证明等级。"""
    try:
        action()
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        return False
    return True


def _restore_safety_dropin(path: Path, run: CommandRunner) -> None:
    """重建 Restart=no，并在 reload 后再次绑定可信原像。"""
    _write_dropin(path)
    _run_systemctl(run, ("systemctl", "daemon-reload"))
    _require_owned_dropin(path)


def _default_stop_proof() -> None:
    from codev_platform.ops.reindex_admin_systemd_guard import verify_codev_reindex_stopped

    verify_codev_reindex_stopped()


def _default_external_worker_proof() -> None:
    from codev_platform.reindex.external_worker_guard import (
        assert_no_external_reindex_writers,
    )

    assert_no_external_reindex_writers()


def _default_codegraph_prepare() -> None:
    """默认收敛 CodeGraph 永久 guard、hold、runtime mask 与固定 unit。"""
    from codev_platform.ops.reindex_codegraph_lifecycle import (
        prepare_codegraph_maintenance,
    )

    prepare_codegraph_maintenance()


def _default_codegraph_maintenance_proof() -> None:
    """默认要求 CodeGraph 跨重启门禁与当前 cgroup 停机均可证明。"""
    from codev_platform.ops.reindex_codegraph_lifecycle import (
        verify_codegraph_maintenance,
    )

    verify_codegraph_maintenance()


def _default_webhook_prepare(
    *,
    platform_name: str | None = None,
    command_runner: CommandRunner | None = None,
) -> None:
    """默认建立 Webhook 跨重启 hold 并收敛完整 cgroup。"""
    from codev_platform.ops.reindex_webhook_maintenance import (
        prepare_webhook_maintenance,
    )

    prepare_webhook_maintenance(
        platform_name=platform_name,
        command_runner=command_runner,
    )


def _default_webhook_maintenance_proof(
    *,
    platform_name: str | None = None,
    command_runner: CommandRunner | None = None,
) -> None:
    """默认只接受 Webhook guard、hold 与停机证明同时成立。"""
    from codev_platform.ops.reindex_webhook_maintenance import (
        verify_webhook_maintenance,
    )

    verify_webhook_maintenance(
        platform_name=platform_name,
        command_runner=command_runner,
    )


def _default_gate_activator() -> None:
    from codev_platform.reindex.maintenance_gate import activate_maintenance_gate

    activate_maintenance_gate()


def _default_gate_deactivator() -> None:
    from codev_platform.reindex.maintenance_gate import deactivate_maintenance_gate

    deactivate_maintenance_gate()


def _default_gate_active_reader() -> bool:
    from codev_platform.reindex.maintenance_gate import maintenance_gate_active

    return maintenance_gate_active()


def _default_gate_record_reader() -> object:
    from codev_platform.reindex.maintenance_gate import read_maintenance_gate_record

    return read_maintenance_gate_record()


__all__ = [
    "ReindexMaintenanceError",
    "cmd_reindex_maintenance",
    "inspect_reindex_maintenance",
    "prepare_reindex_maintenance",
    "prove_reindex_maintenance_dropin",
    "provision_reindex_maintenance",
    "register",
    "restore_reindex_maintenance",
]
