"""CodeGraph systemd 主 unit 布局迁移的 Linux 外部适配器。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from codev_platform.ops.reindex_codegraph_resume_managed_path import (
    RootOwnedRegularFileSnapshot,
    TrustedManagedPathError,
    read_optional_root_owned_regular_file_snapshot,
)
from codev_platform.ops.systemd_unit_layout_filesystem import (
    create_root_owned_regular_file_atomic_if_absent,
    move_root_owned_regular_file_if_snapshot,
)
from codev_platform.ops.systemd_unit_layout_contract import (
    _CODEGRAPH_UNIT,
    SystemdUnitLayoutMigrationError,
    SystemdUnitRuntime,
    UnitLayoutMigrationPorts,
)


_ENABLE_LINK_DIRECTORY = Path("/etc/systemd/system/multi-user.target.wants")
_RUNTIME_MASK_PATH = Path("/run/systemd/system") / _CODEGRAPH_UNIT
_MAX_UNIT_BYTES = 128 * 1024
_SYSTEMCTL_TIMEOUT_SEC = 15.0


def default_ports() -> UnitLayoutMigrationPorts:
    """构造生产适配器；所有文件写入均受 root dirfd 路径叶子保护。"""
    from codev_platform.core.runtime_interpreter import current_proven_interpreter_path
    from codev_platform.reindex.maintenance_gate import (
        maintenance_gate_active,
        maintenance_systemd_transition_lock,
    )

    return UnitLayoutMigrationPorts(
        transition_lock=maintenance_systemd_transition_lock,
        gate_active=maintenance_gate_active,
        read_snapshot=read_snapshot,
        create_if_absent=create_if_absent,
        move_if_snapshot=move_if_snapshot,
        read_runtime=read_runtime,
        systemctl=run_systemctl,
        read_enable_link=read_enable_link,
        read_runtime_mask_target=read_runtime_mask_target,
        expected_codegraph_python=current_proven_interpreter_path,
    )


def read_snapshot(path: Path) -> RootOwnedRegularFileSnapshot | None:
    """读取受信 root 常规文件原像；链接、不可信权限或路径一律拒绝。"""
    try:
        return read_optional_root_owned_regular_file_snapshot(path, max_bytes=_MAX_UNIT_BYTES)
    except TrustedManagedPathError as error:
        raise SystemdUnitLayoutMigrationError("systemd 主 unit 原像不受信任") from error


def create_if_absent(path: Path, snapshot: RootOwnedRegularFileSnapshot) -> None:
    """只在 canonical 叶子不存在时以原子条件创建，禁止覆盖外部新文件。"""
    try:
        create_root_owned_regular_file_atomic_if_absent(
            path,
            snapshot.content,
            mode=snapshot.mode,
            uid=snapshot.uid,
            gid=snapshot.gid,
        )
    except TrustedManagedPathError as error:
        raise SystemdUnitLayoutMigrationError("systemd 主 unit 无法安全写入") from error


def move_if_snapshot(
    source: Path,
    destination: Path,
    snapshot: RootOwnedRegularFileSnapshot,
) -> None:
    """把验证后的旧叶子移入隔离名，不删除或覆盖任何并发产生的主 unit。"""
    try:
        move_root_owned_regular_file_if_snapshot(source, destination, snapshot)
    except TrustedManagedPathError as error:
        raise SystemdUnitLayoutMigrationError("systemd 主 unit 原像已变化") from error


def read_runtime(name: str) -> SystemdUnitRuntime:
    """读取迁移证明所需的精确 systemd 运行态，缺字段即拒绝。"""
    fields = (
        "FragmentPath",
        "UnitFileState",
        "ActiveState",
        "SubState",
        "Result",
        "ExecMainCode",
        "ExecMainStatus",
        "InvocationID",
        "LoadState",
    )
    result = _run_systemctl_command(
        (
            "systemctl",
            "show",
            name,
            *(f"--property={field}" for field in fields),
        )
    )
    values = _parse_systemctl_properties(result, fields)
    return SystemdUnitRuntime(
        fragment_path=values["FragmentPath"],
        unit_file_state=values["UnitFileState"],
        active_state=values["ActiveState"],
        sub_state=values["SubState"],
        result=values["Result"],
        exec_main_code=values["ExecMainCode"],
        exec_main_status=values["ExecMainStatus"],
        invocation_id=values["InvocationID"],
        load_state=values["LoadState"],
    )


def run_systemctl(command: tuple[str, ...]) -> None:
    """执行单一受控 systemctl 命令，非零返回不带原始输出地失败。"""
    if _run_systemctl_command(command).returncode != 0:
        raise SystemdUnitLayoutMigrationError("systemd 布局迁移命令失败")


def read_enable_link(name: str) -> str:
    """读取固定 enable 链接，保证迁移后的启动来源可被证明。"""
    try:
        return os.readlink(_ENABLE_LINK_DIRECTORY / name)
    except OSError as error:
        raise SystemdUnitLayoutMigrationError("systemd enable 链接无法读取") from error


def read_runtime_mask_target() -> str | None:
    """读取 `/run` runtime mask 链接；任何非链接残留均拒绝猜测。"""
    try:
        return os.readlink(_RUNTIME_MASK_PATH)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise SystemdUnitLayoutMigrationError("CodeGraph runtime mask 状态无法读取") from error


def _run_systemctl_command(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_SYSTEMCTL_TIMEOUT_SEC,
            check=False,
        )
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise SystemdUnitLayoutMigrationError("systemd 布局迁移命令无法执行") from error


def _parse_systemctl_properties(
    result: subprocess.CompletedProcess[str],
    expected: tuple[str, ...],
) -> dict[str, str]:
    if result.returncode != 0 or type(result.stdout) is not str:
        raise SystemdUnitLayoutMigrationError("systemd unit 运行状态无法读取")
    values: dict[str, str] = {}
    expected_names = set(expected)
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in expected_names or key in values or "\x00" in value:
            raise SystemdUnitLayoutMigrationError("systemd unit 运行状态格式无效")
        values[key] = value
    if set(values) != expected_names:
        raise SystemdUnitLayoutMigrationError("systemd unit 运行状态不完整")
    return values


__all__ = ["default_ports"]
