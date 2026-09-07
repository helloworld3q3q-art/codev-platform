"""两个受管 unit 的 CodeGraph 恢复配置安装叶子。"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from codev_platform.ops.reindex_codegraph_resume_contract import (
    CODEGRAPH_RESUME_DROPIN,
    REINDEX_RESUME_DROPIN,
    RESUME_ENVIRONMENT_FILE,
    codegraph_resume_environment_files_reason_label,
    codegraph_resume_failure_stage_label,
    codegraph_resume_proof_dropin_active_label,
    codegraph_resume_proof_location_label,
)
from codev_platform.ops.reindex_codegraph_resume_managed_path import (
    RootOwnedRegularFileSnapshot,
)

_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SYSTEMCTL_TIMEOUT_SEC = 10.0
_MAX_MANAGED_FILE_BYTES = 128 * 1024


class CodegraphResumeUnitConfigurationError(RuntimeError):
    """两个固定 unit 的恢复配置无法安全安装或证明。"""

    def __init__(
        self,
        message: str,
        *,
        stage: str | None = None,
        proof_unit: str | None = None,
        proof_property: str | None = None,
        proof_dropin_active: bool | None = None,
        proof_environment_files_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.proof_unit = proof_unit
        self.proof_property = proof_property
        self.proof_dropin_active = proof_dropin_active
        self.proof_environment_files_reason = proof_environment_files_reason


FileWriter = Callable[[Path, bytes, int], None]
FileSnapshotReader = Callable[[Path], RootOwnedRegularFileSnapshot | None]
FileRestorer = Callable[[Path, RootOwnedRegularFileSnapshot | None], None]
SystemdReloader = Callable[[], None]
ConfigurationProof = Callable[..., None]
EffectiveUserId = Callable[[], int]


def configure_codegraph_resume_units(
    *,
    config_path: Path,
    data_root: Path,
    config_digest: str,
    service_environment_path: Path | None = None,
    platform_name: str | None = None,
    effective_user_id: EffectiveUserId | None = None,
    file_writer: FileWriter | None = None,
    file_snapshot_reader: FileSnapshotReader | None = None,
    file_restorer: FileRestorer | None = None,
    systemd_reloader: SystemdReloader | None = None,
    configuration_proof: ConfigurationProof | None = None,
) -> None:
    """以三文件可补偿事务更新共享快照和两个窄 drop-in。"""
    _require_linux_root(platform_name, effective_user_id)
    snapshot = _normalize_snapshot(
        config_path,
        data_root,
        config_digest,
        service_environment_path,
    )
    writer = _atomic_file_writer if file_writer is None else file_writer
    read_snapshot = (
        _default_file_snapshot_reader if file_snapshot_reader is None else file_snapshot_reader
    )
    restore_file = _default_file_restorer if file_restorer is None else file_restorer
    reload_systemd = _default_systemd_reloader if systemd_reloader is None else systemd_reloader
    prove = _default_configuration_proof if configuration_proof is None else configuration_proof
    if not all(
        callable(item) for item in (writer, read_snapshot, restore_file, reload_systemd, prove)
    ):
        raise CodegraphResumeUnitConfigurationError("受管恢复配置适配器不可用")
    files = _managed_files(snapshot)
    originals = _capture_originals(files, read_snapshot)
    stage = "write"
    try:
        for file in files:
            writer(file.path, file.content, file.mode)
        stage = "reload"
        reload_systemd()
        stage = "proof"
        prove(
            config_path=snapshot.config_path,
            data_root=snapshot.data_root,
            config_digest=snapshot.config_digest,
            managed_environment_path=RESUME_ENVIRONMENT_FILE,
        )
    except (KeyboardInterrupt, SystemExit, MemoryError):
        _restore_originals(originals, read_snapshot, restore_file, reload_systemd)
        raise
    except Exception as error:
        restored = _restore_originals(originals, read_snapshot, restore_file, reload_systemd)
        stage_label = codegraph_resume_failure_stage_label(stage)
        if stage_label is None:
            raise CodegraphResumeUnitConfigurationError("受管恢复配置失败阶段无效") from error
        proof = _proof_failure_location(error, stage)
        proof_label = codegraph_resume_proof_location_label(proof.unit, proof.property_name)
        proof_dropin_label = codegraph_resume_proof_dropin_active_label(proof.dropin_active)
        proof_reason_label = codegraph_resume_environment_files_reason_label(
            proof.environment_files_reason
        )
        proof_suffix = "" if proof_label is None else f"；证明={proof_label}"
        if proof_label is not None and proof_dropin_label is not None:
            proof_suffix = f"{proof_suffix}；{proof_dropin_label}"
        if proof_label is not None and proof_reason_label is not None:
            proof_suffix = f"{proof_suffix}；原因={proof_reason_label}"
        message = (
            f"受管恢复配置安装或证明失败；阶段={stage_label}{proof_suffix}；已回滚受管配置"
            if restored
            else f"受管恢复配置安装或证明失败；阶段={stage_label}{proof_suffix}；安全状态未证明"
        )
        raise CodegraphResumeUnitConfigurationError(
            message,
            stage=stage,
            proof_unit=proof.unit,
            proof_property=proof.property_name,
            proof_dropin_active=proof.dropin_active,
            proof_environment_files_reason=proof.environment_files_reason,
        ) from error


class _Snapshot:
    __slots__ = (
        "config_path",
        "data_root",
        "config_digest",
        "service_environment_path",
    )

    def __init__(
        self,
        config_path: Path,
        data_root: Path,
        config_digest: str,
        service_environment_path: Path | None,
    ) -> None:
        self.config_path = config_path
        self.data_root = data_root
        self.config_digest = config_digest
        self.service_environment_path = service_environment_path


@dataclass(frozen=True, slots=True)
class _ManagedFile:
    """单个固定受管文件及其目标内容。"""

    path: Path
    content: bytes
    mode: int


def _require_linux_root(
    platform_name: str | None,
    effective_user_id: EffectiveUserId | None,
) -> None:
    platform = sys.platform if platform_name is None else platform_name
    if type(platform) is not str or not platform.startswith("linux"):
        raise CodegraphResumeUnitConfigurationError("当前平台不支持受管恢复配置安装")
    reader = os.geteuid if effective_user_id is None else effective_user_id
    try:
        if not callable(reader) or reader() != 0:
            raise CodegraphResumeUnitConfigurationError("受管恢复配置安装必须由 root 执行")
    except CodegraphResumeUnitConfigurationError:
        raise
    except Exception:
        raise CodegraphResumeUnitConfigurationError("无法确认受管恢复配置安装身份") from None


def _normalize_snapshot(
    config_path: Path,
    data_root: Path,
    config_digest: str,
    service_environment_path: Path | None,
) -> _Snapshot:
    config = _normalize_existing_file(config_path, "恢复配置覆盖")
    data = _normalize_absolute_path(data_root, "恢复数据根")
    service_environment = _normalize_service_environment_path(service_environment_path)
    if type(config_digest) is not str or _DIGEST.fullmatch(config_digest) is None:
        raise CodegraphResumeUnitConfigurationError("恢复配置摘要无效")
    values = [config.as_posix(), data.as_posix()]
    if service_environment is not None:
        values.append(service_environment.as_posix())
    for value in values:
        if not _safe_environment_value(value):
            raise CodegraphResumeUnitConfigurationError("恢复配置路径无法安全写入 systemd")
    return _Snapshot(config, data, config_digest, service_environment)


def _normalize_service_environment_path(value: Path | None) -> Path | None:
    if value is None:
        return None
    path = _normalize_absolute_path(value, "服务环境文件")
    if path == RESUME_ENVIRONMENT_FILE:
        raise CodegraphResumeUnitConfigurationError("服务环境文件与恢复快照冲突")
    return path


def _normalize_existing_file(value: Path, label: str) -> Path:
    path = _normalize_absolute_path(value, label)
    try:
        if not path.is_file():
            raise CodegraphResumeUnitConfigurationError(f"{label}不可用")
    except CodegraphResumeUnitConfigurationError:
        raise
    except OSError:
        raise CodegraphResumeUnitConfigurationError(f"{label}不可用") from None
    return path


def _normalize_absolute_path(value: Path, label: str) -> Path:
    try:
        path = Path(value)
        if not path.is_absolute():
            raise CodegraphResumeUnitConfigurationError(f"{label}必须为绝对路径")
        return path.resolve()
    except CodegraphResumeUnitConfigurationError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise CodegraphResumeUnitConfigurationError(f"{label}不可用") from None


def _safe_environment_value(value: str) -> bool:
    return (
        bool(value)
        and not any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
        and all(char not in value for char in ("'", '"', "\\"))
    )


def _snapshot_content(snapshot: _Snapshot) -> bytes:
    return (
        f"CODEV_PLATFORM_CONFIG={snapshot.config_path.as_posix()}\n"
        f"PLATFORM_DATA_DIR={snapshot.data_root.as_posix()}\n"
        f"CODEV_REINDEX_CONFIG_SHA256={snapshot.config_digest}\n"
    ).encode()


def render_codegraph_resume_dropin_content(
    service_environment_path: Path | None,
) -> bytes:
    """清空旧来源后，仅追加 root 受管环境与恢复快照。"""
    service_environment = _normalize_service_environment_path(service_environment_path)
    lines = ["[Service]", "EnvironmentFile="]
    if service_environment is not None:
        lines.append(f"EnvironmentFile={service_environment.as_posix()}")
    lines.append(f"EnvironmentFile={RESUME_ENVIRONMENT_FILE.as_posix()}")
    return ("\n".join(lines) + "\n").encode()


def _managed_files(snapshot: _Snapshot) -> tuple[_ManagedFile, ...]:
    """快照最后替换，使跨 unit 激活收敛到一个必需环境文件。"""
    dropin_content = render_codegraph_resume_dropin_content(snapshot.service_environment_path)
    return (
        _ManagedFile(CODEGRAPH_RESUME_DROPIN, dropin_content, 0o644),
        _ManagedFile(REINDEX_RESUME_DROPIN, dropin_content, 0o644),
        _ManagedFile(RESUME_ENVIRONMENT_FILE, _snapshot_content(snapshot), 0o600),
    )


def _capture_originals(
    files: tuple[_ManagedFile, ...],
    read_snapshot: FileSnapshotReader,
) -> tuple[tuple[_ManagedFile, RootOwnedRegularFileSnapshot | None], ...]:
    try:
        originals = tuple((file, read_snapshot(file.path)) for file in files)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise CodegraphResumeUnitConfigurationError("受管恢复配置原像无法读取") from error
    if any(
        file_snapshot is not None and not isinstance(file_snapshot, RootOwnedRegularFileSnapshot)
        for _file, file_snapshot in originals
    ):
        raise CodegraphResumeUnitConfigurationError("受管恢复配置原像无效")
    return originals


def _restore_originals(
    originals: tuple[tuple[_ManagedFile, RootOwnedRegularFileSnapshot | None], ...],
    read_snapshot: FileSnapshotReader,
    restore_file: FileRestorer,
    reload_systemd: SystemdReloader,
) -> bool:
    files_restored = _attempt_all(
        _attempt(
            lambda file=file, file_snapshot=file_snapshot: restore_file(file.path, file_snapshot)
        )
        for file, file_snapshot in reversed(originals)
    )
    reloaded = _attempt(reload_systemd)
    originals_proven = _attempt_all(
        _attempt_equal(lambda file=file: read_snapshot(file.path), file_snapshot)
        for file, file_snapshot in originals
    )
    return files_restored and reloaded and originals_proven


def _attempt(action: Callable[[], object]) -> bool:
    try:
        action()
        return True
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        return False


def _attempt_all(results: Iterable[bool]) -> bool:
    """强制耗尽补偿结果，避免首项失败短路其余恢复。"""
    all_succeeded = True
    for result in results:
        if not result:
            all_succeeded = False
    return all_succeeded


def _attempt_equal(reader: Callable[[], object], expected: object) -> bool:
    try:
        return reader() == expected
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class _ProofFailureMetadata:
    """受控证明失败可跨边界传递的固定元数据。"""

    unit: str | None = None
    property_name: str | None = None
    dropin_active: bool | None = None
    environment_files_reason: str | None = None


def _proof_failure_location(
    error: Exception,
    stage: str,
) -> _ProofFailureMetadata:
    """仅在同源证明阶段接受 proof 模块附带的固定位置元数据。"""
    if stage != "proof":
        return _ProofFailureMetadata()
    from codev_platform.ops.reindex_codegraph_resume_config_proof import (
        CodegraphResumeConfigurationError,
    )

    if not isinstance(error, CodegraphResumeConfigurationError):
        return _ProofFailureMetadata()
    unit = error.unit
    property_name = error.property_name
    if codegraph_resume_proof_location_label(unit, property_name) is None:
        return _ProofFailureMetadata()
    dropin_active = error.dropin_active
    if type(dropin_active) is not bool:
        dropin_active = None
    environment_files_reason = error.environment_files_reason
    if codegraph_resume_environment_files_reason_label(environment_files_reason) is None:
        environment_files_reason = None
    return _ProofFailureMetadata(
        unit=unit,
        property_name=property_name,
        dropin_active=dropin_active,
        environment_files_reason=environment_files_reason,
    )


def _default_file_snapshot_reader(path: Path) -> RootOwnedRegularFileSnapshot | None:
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        TrustedManagedPathError,
        read_optional_root_owned_regular_file_snapshot,
    )

    try:
        return read_optional_root_owned_regular_file_snapshot(
            path, max_bytes=_MAX_MANAGED_FILE_BYTES
        )
    except TrustedManagedPathError as error:
        raise CodegraphResumeUnitConfigurationError("受管恢复配置原像不受信任") from error


def _default_file_restorer(path: Path, file_snapshot: RootOwnedRegularFileSnapshot | None) -> None:
    if file_snapshot is None:
        from codev_platform.ops.reindex_codegraph_resume_managed_path import (
            TrustedManagedPathError,
            remove_root_owned_regular_file,
        )

        try:
            remove_root_owned_regular_file(path)
            return
        except TrustedManagedPathError as error:
            raise CodegraphResumeUnitConfigurationError("受管恢复配置原像无法删除") from error
    _atomic_file_writer(
        path,
        file_snapshot.content,
        file_snapshot.mode,
        uid=file_snapshot.uid,
        gid=file_snapshot.gid,
    )


def _atomic_file_writer(
    path: Path, content: bytes, mode: int, *, uid: int = 0, gid: int = 0
) -> None:
    if (
        type(content) is not bytes
        or type(mode) is not int
        or type(uid) is not int
        or type(gid) is not int
    ):
        raise CodegraphResumeUnitConfigurationError("受管恢复配置写入参数无效")
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        TrustedManagedPathError,
        write_root_owned_regular_file_atomic,
    )

    try:
        write_root_owned_regular_file_atomic(path, content, mode=mode, uid=uid, gid=gid)
    except TrustedManagedPathError as error:
        raise CodegraphResumeUnitConfigurationError("无法原子写入受管恢复配置") from error


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
    except MemoryError:
        raise
    except Exception as error:
        raise CodegraphResumeUnitConfigurationError("systemd 配置重载无法执行") from error
    if result.returncode != 0:
        raise CodegraphResumeUnitConfigurationError("systemd 配置重载失败")


def _default_configuration_proof(**kwargs: object) -> None:
    from codev_platform.ops.reindex_codegraph_resume_config_proof import (
        verify_codegraph_resume_configuration,
    )

    verify_codegraph_resume_configuration(**kwargs)


__all__ = [
    "CodegraphResumeUnitConfigurationError",
    "configure_codegraph_resume_units",
    "render_codegraph_resume_dropin_content",
]
