"""CodeGraph M1 一次性启动桥 drop-in 的受控生命周期。"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from codev_platform.ops.reindex_codegraph_resume_contract import (
    CODEGRAPH_STARTUP_BRIDGE_DROPIN,
)
from codev_platform.ops.reindex_codegraph_resume_managed_path import (
    RootOwnedRegularFileSnapshot,
    TrustedManagedPathError,
    read_optional_root_owned_regular_file_snapshot,
    remove_root_owned_regular_file,
    write_root_owned_regular_file_atomic,
)


_DROPIN_MODE = 0o644
_MAX_BRIDGE_BYTES = 128 * 1024
_SYSTEMCTL_TIMEOUT_SEC = 10.0


class CodegraphStartupBridgeError(RuntimeError):
    """一次性启动桥无法在受控 M1 边界内安装、删除或证明。"""


SnapshotReader = Callable[[Path], RootOwnedRegularFileSnapshot | None]
FileWriter = Callable[[Path, bytes, int], None]
FileRemover = Callable[[Path], bool]
SystemdReloader = Callable[[], None]


@dataclass(frozen=True, slots=True)
class CodegraphStartupBridgeSpec:
    """绑定 frozen interpreter、root bridge 源文件和唯一 CodeGraph 端口。"""

    interpreter: PurePosixPath
    bridge_path: PurePosixPath
    port: int

    def __post_init__(self) -> None:
        interpreter = _require_absolute_posix_path(self.interpreter, "CodeGraph bridge 解释器")
        bridge = _require_absolute_posix_path(self.bridge_path, "CodeGraph bridge 文件")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise CodegraphStartupBridgeError("CodeGraph bridge 端口无效")
        object.__setattr__(self, "interpreter", interpreter)
        object.__setattr__(self, "bridge_path", bridge)

    @property
    def exec_start(self) -> tuple[str, ...]:
        """直接执行受信 bridge 脚本，避免 systemd 解析 Python ``-c`` 文本。"""
        return (
            self.interpreter.as_posix(),
            "-I",
            "-B",
            self.bridge_path.as_posix(),
            "--http",
            "--port",
            str(self.port),
        )

    @property
    def dropin_content(self) -> bytes:
        return (f"[Service]\nExecStart=\nExecStart={' '.join(self.exec_start)}\n").encode()

    @property
    def snapshot(self) -> RootOwnedRegularFileSnapshot:
        return RootOwnedRegularFileSnapshot(
            content=self.dropin_content,
            mode=_DROPIN_MODE,
            uid=0,
            gid=0,
        )


def current_codegraph_startup_bridge_spec(
    interpreter: Path | PurePosixPath | str,
    port: int,
) -> CodegraphStartupBridgeSpec:
    """从当前 root controller 同目录解析 bridge，不接受调用方任意脚本路径。"""
    return CodegraphStartupBridgeSpec(
        interpreter=interpreter,
        bridge_path=Path(__file__).resolve().with_name("codegraph_startup_bridge.py"),
        port=port,
    )


def install_codegraph_startup_bridge(
    spec: CodegraphStartupBridgeSpec,
    *,
    platform_name: str | None = None,
    effective_user_id: Callable[[], int] | None = None,
    snapshot_reader: SnapshotReader | None = None,
    file_writer: FileWriter | None = None,
    file_remover: FileRemover | None = None,
    systemd_reloader: SystemdReloader | None = None,
) -> None:
    """安装 M1 专用 ExecStart 覆盖；未知既有文件一律拒绝覆盖。"""
    _require_linux_root(platform_name, effective_user_id)
    bridge = _require_spec(spec)
    reader = _default_snapshot_reader if snapshot_reader is None else snapshot_reader
    writer = _default_file_writer if file_writer is None else file_writer
    remover = _default_file_remover if file_remover is None else file_remover
    reload_systemd = _default_systemd_reloader if systemd_reloader is None else systemd_reloader
    if not all(callable(item) for item in (reader, writer, remover, reload_systemd)):
        raise CodegraphStartupBridgeError("CodeGraph bridge 安装适配器不可用")
    _require_trusted_bridge_source(bridge, reader)
    original = _read_snapshot(reader)
    expected = bridge.snapshot
    if original is not None and not _matches(original, expected):
        raise CodegraphStartupBridgeError("CodeGraph bridge 既有 drop-in 不受信任")
    wrote = original is None
    try:
        if wrote:
            writer(CODEGRAPH_STARTUP_BRIDGE_DROPIN, expected.content, expected.mode)
        reload_systemd()
        _require_installed_snapshot(reader, expected)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        _restore_after_install_failure(original, reader, writer, remover, reload_systemd)
        raise
    except Exception as error:
        restored = _restore_after_install_failure(
            original,
            reader,
            writer,
            remover,
            reload_systemd,
        )
        message = (
            "CodeGraph bridge 安装失败；已恢复原像"
            if restored
            else "CodeGraph bridge 安装失败；安全状态未证明"
        )
        raise CodegraphStartupBridgeError(message) from error


def clear_codegraph_startup_bridge(
    spec: CodegraphStartupBridgeSpec,
    *,
    platform_name: str | None = None,
    effective_user_id: Callable[[], int] | None = None,
    snapshot_reader: SnapshotReader | None = None,
    file_writer: FileWriter | None = None,
    file_remover: FileRemover | None = None,
    systemd_reloader: SystemdReloader | None = None,
) -> None:
    """M0 收敛时仅清除精确 bridge；缺失时幂等通过。"""
    _remove_expected_bridge(
        spec,
        allow_absent=True,
        platform_name=platform_name,
        effective_user_id=effective_user_id,
        snapshot_reader=snapshot_reader,
        file_writer=file_writer,
        file_remover=file_remover,
        systemd_reloader=systemd_reloader,
    )


def remove_codegraph_startup_bridge(
    spec: CodegraphStartupBridgeSpec,
    *,
    platform_name: str | None = None,
    effective_user_id: Callable[[], int] | None = None,
    snapshot_reader: SnapshotReader | None = None,
    file_writer: FileWriter | None = None,
    file_remover: FileRemover | None = None,
    systemd_reloader: SystemdReloader | None = None,
) -> None:
    """成功 M1 后移除已安装 bridge；缺失一律拒绝。"""
    _remove_expected_bridge(
        spec,
        allow_absent=False,
        platform_name=platform_name,
        effective_user_id=effective_user_id,
        snapshot_reader=snapshot_reader,
        file_writer=file_writer,
        file_remover=file_remover,
        systemd_reloader=systemd_reloader,
    )


def _remove_expected_bridge(
    spec: CodegraphStartupBridgeSpec,
    *,
    allow_absent: bool,
    platform_name: str | None,
    effective_user_id: Callable[[], int] | None,
    snapshot_reader: SnapshotReader | None,
    file_writer: FileWriter | None,
    file_remover: FileRemover | None,
    systemd_reloader: SystemdReloader | None,
) -> None:
    """删除精确 bridge，失败时复原同一原像且从不覆盖未知文件。"""
    if type(allow_absent) is not bool:
        raise CodegraphStartupBridgeError("CodeGraph bridge 清理策略无效")
    _require_linux_root(platform_name, effective_user_id)
    bridge = _require_spec(spec)
    reader = _default_snapshot_reader if snapshot_reader is None else snapshot_reader
    writer = _default_file_writer if file_writer is None else file_writer
    remover = _default_file_remover if file_remover is None else file_remover
    reload_systemd = _default_systemd_reloader if systemd_reloader is None else systemd_reloader
    if not all(callable(item) for item in (reader, writer, remover, reload_systemd)):
        raise CodegraphStartupBridgeError("CodeGraph bridge 删除适配器不可用")
    expected = bridge.snapshot
    original = _read_snapshot(reader)
    if original is None:
        if allow_absent:
            return
        raise CodegraphStartupBridgeError("CodeGraph bridge 删除原像无法证明")
    if not _matches(original, expected):
        raise CodegraphStartupBridgeError("CodeGraph bridge 删除原像无法证明")
    removed = False
    try:
        removed = remover(CODEGRAPH_STARTUP_BRIDGE_DROPIN) is True
        if not removed:
            raise CodegraphStartupBridgeError("CodeGraph bridge 删除未生效")
        reload_systemd()
        if _read_snapshot(reader) is not None:
            raise CodegraphStartupBridgeError("CodeGraph bridge 删除后仍存在")
    except (KeyboardInterrupt, SystemExit, MemoryError):
        _restore_after_remove_failure(expected, reader, writer, reload_systemd, removed)
        raise
    except Exception as error:
        restored = _restore_after_remove_failure(
            expected,
            reader,
            writer,
            reload_systemd,
            removed,
        )
        message = (
            "CodeGraph bridge 删除失败；已恢复原像"
            if restored
            else "CodeGraph bridge 删除失败；安全状态未证明"
        )
        raise CodegraphStartupBridgeError(message) from error


def _require_spec(value: object) -> CodegraphStartupBridgeSpec:
    if type(value) is not CodegraphStartupBridgeSpec:
        raise CodegraphStartupBridgeError("CodeGraph bridge 规格无效")
    return value


def _require_absolute_posix_path(value: object, label: str) -> PurePosixPath:
    try:
        raw = value.as_posix() if isinstance(value, (Path, PurePosixPath)) else str(value)
        path = PurePosixPath(raw)
        text = path.as_posix()
        if (
            not path.is_absolute()
            or text != raw
            or text.startswith("//")
            or ".." in path.parts
            or "\x00" in raw
            or any(char.isspace() for char in raw)
        ):
            raise ValueError
        return path
    except (OSError, RuntimeError, TypeError, ValueError):
        raise CodegraphStartupBridgeError(f"{label}无效") from None


def _require_linux_root(
    platform_name: str | None,
    effective_user_id: Callable[[], int] | None,
) -> None:
    platform = sys.platform if platform_name is None else platform_name
    reader = os.geteuid if effective_user_id is None else effective_user_id
    try:
        if not isinstance(platform, str) or not platform.startswith("linux") or reader() != 0:
            raise CodegraphStartupBridgeError("CodeGraph bridge 必须由 Linux root 管理")
    except CodegraphStartupBridgeError:
        raise
    except Exception:
        raise CodegraphStartupBridgeError("CodeGraph bridge 管理身份无法证明") from None


def _require_trusted_bridge_source(
    spec: CodegraphStartupBridgeSpec,
    reader: SnapshotReader,
) -> None:
    """复用受管读取器的 root 所有且不可被非属主写入证明。"""
    try:
        snapshot = reader(Path(spec.bridge_path))
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise CodegraphStartupBridgeError("CodeGraph bridge 源文件无法读取") from error
    if type(snapshot) is not RootOwnedRegularFileSnapshot or not snapshot.content:
        raise CodegraphStartupBridgeError("CodeGraph bridge 源文件不受信任")


def _read_snapshot(reader: SnapshotReader) -> RootOwnedRegularFileSnapshot | None:
    try:
        snapshot = reader(CODEGRAPH_STARTUP_BRIDGE_DROPIN)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise CodegraphStartupBridgeError("CodeGraph bridge drop-in 原像无法读取") from error
    if snapshot is not None and not isinstance(snapshot, RootOwnedRegularFileSnapshot):
        raise CodegraphStartupBridgeError("CodeGraph bridge drop-in 原像无效")
    return snapshot


def _matches(
    actual: RootOwnedRegularFileSnapshot,
    expected: RootOwnedRegularFileSnapshot,
) -> bool:
    return actual.matches(expected, require_identity=False)


def _require_installed_snapshot(
    reader: SnapshotReader,
    expected: RootOwnedRegularFileSnapshot,
) -> None:
    current = _read_snapshot(reader)
    if current is None or not _matches(current, expected):
        raise CodegraphStartupBridgeError("CodeGraph bridge drop-in 未证明")


def _restore_after_install_failure(
    original: RootOwnedRegularFileSnapshot | None,
    reader: SnapshotReader,
    writer: FileWriter,
    remover: FileRemover,
    reload_systemd: SystemdReloader,
) -> bool:
    try:
        if original is None:
            remover(CODEGRAPH_STARTUP_BRIDGE_DROPIN)
        else:
            writer(CODEGRAPH_STARTUP_BRIDGE_DROPIN, original.content, original.mode)
        reload_systemd()
        current = _read_snapshot(reader)
        return (
            current is None
            if original is None
            else _matches(current, original)
            if current
            else False
        )
    except Exception:
        return False


def _restore_after_remove_failure(
    expected: RootOwnedRegularFileSnapshot,
    reader: SnapshotReader,
    writer: FileWriter,
    reload_systemd: SystemdReloader,
    removed: bool,
) -> bool:
    if not removed:
        current = _read_snapshot(reader)
        return current is not None and _matches(current, expected)
    try:
        writer(CODEGRAPH_STARTUP_BRIDGE_DROPIN, expected.content, expected.mode)
        reload_systemd()
        current = _read_snapshot(reader)
        return current is not None and _matches(current, expected)
    except Exception:
        return False


def _default_snapshot_reader(path: Path) -> RootOwnedRegularFileSnapshot | None:
    try:
        return read_optional_root_owned_regular_file_snapshot(path, max_bytes=_MAX_BRIDGE_BYTES)
    except TrustedManagedPathError as error:
        raise CodegraphStartupBridgeError("CodeGraph bridge 受管路径不受信任") from error


def _default_file_writer(path: Path, content: bytes, mode: int) -> None:
    try:
        write_root_owned_regular_file_atomic(path, content, mode=mode, uid=0, gid=0)
    except TrustedManagedPathError as error:
        raise CodegraphStartupBridgeError("CodeGraph bridge drop-in 无法安全写入") from error


def _default_file_remover(path: Path) -> bool:
    try:
        return remove_root_owned_regular_file(path)
    except TrustedManagedPathError as error:
        raise CodegraphStartupBridgeError("CodeGraph bridge drop-in 无法安全删除") from error


def _default_systemd_reloader() -> None:
    try:
        result = subprocess.run(
            ("systemctl", "daemon-reload"),
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
        raise CodegraphStartupBridgeError("CodeGraph bridge systemd 重载失败") from error
    if result.returncode != 0:
        raise CodegraphStartupBridgeError("CodeGraph bridge systemd 重载失败")


__all__ = [
    "CodegraphStartupBridgeError",
    "CodegraphStartupBridgeSpec",
    "clear_codegraph_startup_bridge",
    "current_codegraph_startup_bridge_spec",
    "install_codegraph_startup_bridge",
    "remove_codegraph_startup_bridge",
]
