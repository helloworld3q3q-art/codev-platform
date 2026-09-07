"""maintenance-stage 发布回执的构建、可信持久化与目标版本读取。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path

from codev_platform.core.runtime_interpreter import ReleaseInterpreterIdentity
from codev_platform.mcp_systemd_effective_payload import (
    parse_unit_exec_start,
    verify_codegraph_maintenance_condition,
)
from codev_platform.mcp_systemd_install_contract import (
    CODEGRAPH_SYSTEMD_UNIT_NAME,
    REINDEX_SYSTEMD_UNIT_NAME,
    SystemdInstallManifest,
    SystemdInstallTransactionError,
    SystemdStageReceiptFileSnapshot,
    SystemdUnitActivationMode,
    SystemdUnitFileSnapshot,
    SystemdUnitInstallSpec,
    SystemdUnitPayload,
    require_runtime_revision,
    require_unit_activation_mode,
)
from codev_platform.mcp_systemd_unit_registry import MAINTENANCE_DEFERRED_UNITS


STAGE_RECEIPT_PATH = Path("/var/lib/codev-platform/systemd-stage-receipt.json")
_SCHEMA_VERSION = 3
_RECEIPT_MODE = 0o600
_MAX_RECEIPT_BYTES = 16 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PROTECTED_UNITS = (REINDEX_SYSTEMD_UNIT_NAME, CODEGRAPH_SYSTEMD_UNIT_NAME)
_REQUIRED_ENABLED_UNITS = MAINTENANCE_DEFERRED_UNITS
_REINDEX_EXEC_TAIL = (
    "-I",
    "-m",
    "codev_platform.cli",
    "reindex-queue",
    "worker",
    "--require-execution-mode",
    "isolated",
)
_CODEGRAPH_EXEC_PREFIX = (
    "-I",
    "-m",
    "codev_platform.codegraph.server",
    "--http",
    "--port",
)
_CANONICAL_UNIT_PATHS = {
    REINDEX_SYSTEMD_UNIT_NAME: Path("/etc/systemd/system") / REINDEX_SYSTEMD_UNIT_NAME,
    CODEGRAPH_SYSTEMD_UNIT_NAME: (
        Path("/usr/local/lib/systemd/system") / CODEGRAPH_SYSTEMD_UNIT_NAME
    ),
}


class StageReceiptError(SystemdInstallTransactionError):
    """stage 回执无法证明与目标提交、清单和受保护载荷一致。"""


StageReceiptSnapshotReader = Callable[[], SystemdStageReceiptFileSnapshot | None]
InstalledUnitSnapshotReader = Callable[[str], SystemdUnitFileSnapshot | None]
EffectivePayloadVerifier = Callable[[tuple[SystemdUnitPayload, ...]], None]
UnitSourceResolver = Callable[[str], Path]
UnitEnabledProbe = Callable[[str], bool]


def build_stage_receipt_content(
    manifest: SystemdInstallManifest,
    runtime_release: ReleaseInterpreterIdentity,
) -> bytes:
    """构建绑定完整发布身份和两个受保护 unit 的规范回执。"""
    checked = _require_manifest(manifest)
    release = _require_release_identity(runtime_release)
    if checked.runtime_revision != release.runtime_revision:
        raise StageReceiptError("stage manifest 与实际发布运行时不一致")
    digests = _protected_unit_digests(checked)
    payload = {
        "schema": _SCHEMA_VERSION,
        "runtime": _runtime_mapping(release),
        "protected_units": digests,
        "enabled_units": list(_REQUIRED_ENABLED_UNITS),
    }
    return _canonical_json(payload) + b"\n"


def read_stage_receipt_expected_digests(
    runtime_release: ReleaseInterpreterIdentity,
    *,
    snapshot_reader: StageReceiptSnapshotReader | None = None,
) -> dict[str, str]:
    """只在可信回执精确绑定完整发布身份时返回 unit 期望摘要。"""
    expected_release = _require_release_identity(runtime_release)
    snapshot = _read_snapshot(snapshot_reader)
    _require_trusted_receipt_snapshot(snapshot)
    payload = _parse_receipt(snapshot.content)
    if payload["runtime"] != _runtime_mapping(expected_release):
        raise StageReceiptError("stage 回执与发布运行身份不一致")
    protected = payload["protected_units"]
    return {name: protected[name] for name in _PROTECTED_UNITS}


def verify_stage_receipt_content(
    expected_content: bytes,
    *,
    snapshot_reader: StageReceiptSnapshotReader | None = None,
) -> None:
    """复证持久化回执的内容、权限与属主均与事务期望完全一致。"""
    try:
        _parse_receipt(expected_content)
        snapshot = _read_snapshot(snapshot_reader)
        _require_trusted_receipt_snapshot(snapshot)
    except StageReceiptError:
        raise
    if snapshot.content != expected_content:
        raise StageReceiptError("stage 回执复证失败：内容摘要错配")


def prove_staged_systemd_payload(
    runtime_release: ReleaseInterpreterIdentity,
    *,
    require_effective: bool,
    resume_dropin_content: bytes | None = None,
    allow_reindex_local_dropins: bool = False,
    codegraph_port: int | None = None,
    codegraph_startup_bridge_dropin_content: bytes | None = None,
    codegraph_effective_exec_start: tuple[str, ...] | None = None,
    snapshot_reader: StageReceiptSnapshotReader | None = None,
    unit_snapshot_reader: InstalledUnitSnapshotReader | None = None,
    unit_source_resolver: UnitSourceResolver | None = None,
    effective_payload_verifier: EffectivePayloadVerifier | None = None,
    unit_enabled_probe: UnitEnabledProbe | None = None,
) -> None:
    """按恢复阶段证明目标回执、canonical 原像及可选 effective payload。"""
    if type(require_effective) is not bool or type(allow_reindex_local_dropins) is not bool:
        raise StageReceiptError("stage 载荷证明阶段无效")
    expected = read_stage_receipt_expected_digests(
        runtime_release,
        snapshot_reader=snapshot_reader,
    )
    _prove_enabled_units(unit_enabled_probe)
    reader = _select_unit_snapshot_reader(unit_snapshot_reader)
    resolver = _select_unit_source_resolver(unit_source_resolver)
    payloads = tuple(
        _canonical_payload(name, expected[name], reader, resolver) for name in _PROTECTED_UNITS
    )
    verify_stage_payload_runtime_identity(
        payloads,
        runtime_release,
        codegraph_port=codegraph_port,
    )
    if not require_effective:
        if (
            codegraph_startup_bridge_dropin_content is not None
            or codegraph_effective_exec_start is not None
        ):
            raise StageReceiptError("stage 非有效载荷阶段不接受 CodeGraph bridge")
        return
    expected_dropin = _require_resume_dropin_content(resume_dropin_content)
    verifier = _select_effective_payload_verifier(
        effective_payload_verifier,
        expected_dropin,
        allow_reindex_local_dropins=allow_reindex_local_dropins,
        codegraph_startup_bridge_dropin_content=codegraph_startup_bridge_dropin_content,
        codegraph_effective_exec_start=codegraph_effective_exec_start,
    )
    try:
        verifier(payloads)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except StageReceiptError:
        raise
    except Exception as error:
        raise StageReceiptError("stage effective systemd 载荷无法证明") from error


def verify_stage_payload_runtime_identity(
    payloads: tuple[SystemdUnitPayload, ...],
    runtime_release: ReleaseInterpreterIdentity,
    *,
    codegraph_port: int | None = None,
) -> None:
    """证明两个写服务均由当前 release 解释器和固定入口启动。"""
    release = _require_release_identity(runtime_release)
    expected_codegraph_port = _require_optional_codegraph_port(codegraph_port)
    if type(payloads) is not tuple or not all(
        type(payload) is SystemdUnitPayload for payload in payloads
    ):
        raise StageReceiptError("stage systemd 载荷无效")
    selected = tuple(payload for payload in payloads if payload.spec.unit_name in _PROTECTED_UNITS)
    protected = {payload.spec.unit_name: payload for payload in selected}
    if len(selected) != len(_PROTECTED_UNITS) or set(protected) != set(_PROTECTED_UNITS):
        raise StageReceiptError("stage systemd 载荷缺少唯一受保护 unit")
    for name in _PROTECTED_UNITS:
        payload = protected[name]
        argv = _protected_exec_start(payload)
        if argv[0] != release.interpreter_path:
            raise StageReceiptError("stage 受保护 unit 未绑定当前发布解释器")
        if payload.spec.unit_name == REINDEX_SYSTEMD_UNIT_NAME:
            if argv[1:] != _REINDEX_EXEC_TAIL:
                raise StageReceiptError("stage reindex unit 启动入口无效")
            continue
        actual_codegraph_port = _require_codegraph_exec_tail(argv[1:])
        if expected_codegraph_port is not None and actual_codegraph_port != expected_codegraph_port:
            raise StageReceiptError("stage CodeGraph unit 端口与恢复健康端口不一致")
        try:
            verify_codegraph_maintenance_condition(payload)
        except SystemdInstallTransactionError as error:
            raise StageReceiptError(str(error)) from error


def _protected_exec_start(payload: SystemdUnitPayload) -> tuple[str, ...]:
    try:
        argv = parse_unit_exec_start(payload)
    except SystemdInstallTransactionError as error:
        raise StageReceiptError("stage 受保护 unit ExecStart 无效") from error
    if argv is None:
        raise StageReceiptError("stage 受保护 unit ExecStart 无效")
    return argv


def _require_codegraph_exec_tail(argv: tuple[str, ...]) -> int:
    if len(argv) != len(_CODEGRAPH_EXEC_PREFIX) + 1:
        raise StageReceiptError("stage CodeGraph unit 启动入口无效")
    if argv[:-1] != _CODEGRAPH_EXEC_PREFIX:
        raise StageReceiptError("stage CodeGraph unit 启动入口无效")
    try:
        port = int(argv[-1], 10)
    except (TypeError, ValueError):
        raise StageReceiptError("stage CodeGraph unit 端口无效") from None
    if str(port) != argv[-1] or not 1 <= port <= 65535:
        raise StageReceiptError("stage CodeGraph unit 端口无效")
    return port


def _require_optional_codegraph_port(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 1 <= value <= 65535:
        raise StageReceiptError("stage CodeGraph 健康端口无效")
    return value


def default_stage_receipt_snapshot_reader() -> SystemdStageReceiptFileSnapshot | None:
    """从固定 root 可信路径读取可恢复原像。"""
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        TrustedManagedPathError,
        read_optional_root_owned_regular_file_snapshot,
    )

    try:
        snapshot = read_optional_root_owned_regular_file_snapshot(
            STAGE_RECEIPT_PATH,
            max_bytes=_MAX_RECEIPT_BYTES,
        )
    except TrustedManagedPathError as error:
        raise StageReceiptError("stage 回执路径不受信任") from error
    if snapshot is None:
        return None
    return SystemdStageReceiptFileSnapshot(
        content=snapshot.content,
        mode=snapshot.mode,
        uid=snapshot.uid,
        gid=snapshot.gid,
    )


def default_stage_receipt_writer(content: bytes) -> None:
    """以 0600/root:root 原子写入固定可信路径。"""
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        TrustedManagedPathError,
        write_root_owned_regular_file_atomic,
    )

    _parse_receipt(content)
    try:
        write_root_owned_regular_file_atomic(
            STAGE_RECEIPT_PATH,
            content,
            mode=_RECEIPT_MODE,
            uid=0,
            gid=0,
        )
    except TrustedManagedPathError as error:
        raise StageReceiptError("stage 回执无法安全写入") from error


def default_stage_receipt_restorer(
    snapshot: SystemdStageReceiptFileSnapshot | None,
) -> None:
    """失败补偿时精确恢复旧回执；原先不存在则安全删除。"""
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        TrustedManagedPathError,
        remove_root_owned_regular_file,
        write_root_owned_regular_file_atomic,
    )

    try:
        if snapshot is None:
            remove_root_owned_regular_file(STAGE_RECEIPT_PATH)
            return
        if not isinstance(snapshot, SystemdStageReceiptFileSnapshot):
            raise StageReceiptError("stage 回执原像无效")
        write_root_owned_regular_file_atomic(
            STAGE_RECEIPT_PATH,
            snapshot.content,
            mode=snapshot.mode,
            uid=snapshot.uid,
            gid=snapshot.gid,
        )
    except TrustedManagedPathError as error:
        raise StageReceiptError("stage 回执原像无法恢复") from error


def default_stage_receipt_verifier(content: bytes) -> None:
    """生产端口的固定路径复证。"""
    verify_stage_receipt_content(content)


def _require_manifest(manifest: object) -> SystemdInstallManifest:
    if type(manifest) is not SystemdInstallManifest:
        raise StageReceiptError("stage 回执 manifest 无效")
    try:
        require_runtime_revision(manifest.runtime_revision, git_only=True)
        for unit in manifest.units:
            require_unit_activation_mode(unit)
    except SystemdInstallTransactionError as error:
        raise StageReceiptError(str(error)) from error
    return manifest


def _require_release_identity(value: object) -> ReleaseInterpreterIdentity:
    if type(value) is not ReleaseInterpreterIdentity:
        raise StageReceiptError("stage 发布运行身份无效")
    try:
        return ReleaseInterpreterIdentity(
            runtime_revision=value.runtime_revision,
            release_id=value.release_id,
            interpreter_path=value.interpreter_path,
        )
    except (TypeError, ValueError) as error:
        raise StageReceiptError("stage 发布运行身份无效") from error


def _runtime_mapping(value: ReleaseInterpreterIdentity) -> dict[str, str]:
    return {
        "interpreter_path": value.interpreter_path,
        "release_id": value.release_id,
        "runtime_revision": value.runtime_revision,
    }


def _protected_unit_digests(manifest: SystemdInstallManifest) -> dict[str, str]:
    by_name = {unit.unit_name: unit.content_digest for unit in manifest.units}
    if any(name not in by_name for name in _PROTECTED_UNITS):
        raise StageReceiptError("stage 回执缺少受保护 systemd unit")
    return {name: by_name[name] for name in _PROTECTED_UNITS}


def _read_snapshot(
    reader: StageReceiptSnapshotReader | None,
) -> SystemdStageReceiptFileSnapshot:
    selected = default_stage_receipt_snapshot_reader if reader is None else reader
    if not callable(selected):
        raise StageReceiptError("stage 回执读取器不可用")
    try:
        snapshot = selected()
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except StageReceiptError:
        raise
    except Exception as error:
        raise StageReceiptError("stage 回执无法安全读取") from error
    if not isinstance(snapshot, SystemdStageReceiptFileSnapshot):
        raise StageReceiptError("stage 回执不存在或原像无效")
    return snapshot


def _require_trusted_receipt_snapshot(snapshot: SystemdStageReceiptFileSnapshot) -> None:
    if (snapshot.mode, snapshot.uid, snapshot.gid) != (_RECEIPT_MODE, 0, 0):
        raise StageReceiptError("stage 回执权限或所有者不受信任")


def _select_unit_snapshot_reader(
    reader: InstalledUnitSnapshotReader | None,
) -> InstalledUnitSnapshotReader:
    if reader is None:
        from codev_platform.mcp_systemd_install_systemd import default_unit_snapshot_reader

        return default_unit_snapshot_reader
    if not callable(reader):
        raise StageReceiptError("stage canonical unit 读取器不可用")
    return reader


def _select_effective_payload_verifier(
    verifier: EffectivePayloadVerifier | None,
    resume_dropin_content: bytes,
    *,
    allow_reindex_local_dropins: bool,
    codegraph_startup_bridge_dropin_content: bytes | None,
    codegraph_effective_exec_start: tuple[str, ...] | None,
) -> EffectivePayloadVerifier:
    if verifier is None:
        from codev_platform.mcp_systemd_install_systemd import (
            default_staged_effective_unit_payload_verifier,
        )

        return lambda payloads: default_staged_effective_unit_payload_verifier(
            payloads,
            resume_dropin_content=resume_dropin_content,
            allow_reindex_local_dropins=allow_reindex_local_dropins,
            codegraph_startup_bridge_dropin_content=codegraph_startup_bridge_dropin_content,
            codegraph_effective_exec_start=codegraph_effective_exec_start,
        )
    if not callable(verifier):
        raise StageReceiptError("stage effective payload 证明器不可用")
    return verifier


def _require_resume_dropin_content(value: object) -> bytes:
    if type(value) is not bytes or not value or len(value) > 128 * 1024:
        raise StageReceiptError("stage 受管恢复 drop-in 内容无效")
    return value


def _select_unit_source_resolver(
    resolver: UnitSourceResolver | None,
) -> UnitSourceResolver:
    if resolver is None:
        return _CANONICAL_UNIT_PATHS.__getitem__
    if not callable(resolver):
        raise StageReceiptError("stage canonical unit 路径解析器不可用")
    return resolver


def _canonical_payload(
    name: str,
    expected_digest: str,
    reader: InstalledUnitSnapshotReader,
    resolver: UnitSourceResolver,
) -> SystemdUnitPayload:
    try:
        snapshot = reader(name)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise StageReceiptError("stage canonical systemd unit 无法读取") from error
    if (
        not isinstance(snapshot, SystemdUnitFileSnapshot)
        or (snapshot.mode, snapshot.uid, snapshot.gid) != (0o644, 0, 0)
        or hashlib.sha256(snapshot.content).hexdigest() != expected_digest
    ):
        raise StageReceiptError("stage canonical systemd unit 摘要或元数据不匹配")
    mode = (
        SystemdUnitActivationMode.REINDEX_STATE_MACHINE
        if name == REINDEX_SYSTEMD_UNIT_NAME
        else SystemdUnitActivationMode.CODEGRAPH_STATE_MACHINE
    )
    try:
        spec = SystemdUnitInstallSpec(
            source=resolver(name),
            content_digest=expected_digest,
            enable=True,
            restart=True,
            activation_mode=mode,
        )
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise StageReceiptError("stage canonical systemd unit 路径无效") from error
    return SystemdUnitPayload(spec=spec, content=snapshot.content)


def _parse_receipt(content: object) -> dict[str, object]:
    if type(content) is not bytes or not content or len(content) > _MAX_RECEIPT_BYTES:
        raise StageReceiptError("stage 回执内容无效")
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise StageReceiptError("stage 回执内容无效") from None
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "runtime",
        "protected_units",
        "enabled_units",
    }:
        raise StageReceiptError("stage 回执结构无效")
    if type(payload["schema"]) is not int or payload["schema"] != _SCHEMA_VERSION:
        raise StageReceiptError("stage 回执版本无效")
    runtime = payload["runtime"]
    protected = payload["protected_units"]
    enabled = payload["enabled_units"]
    if not isinstance(runtime, dict) or set(runtime) != {
        "runtime_revision",
        "release_id",
        "interpreter_path",
    }:
        raise StageReceiptError("stage 回执发布运行身份无效")
    try:
        release = ReleaseInterpreterIdentity(
            runtime_revision=runtime["runtime_revision"],
            release_id=runtime["release_id"],
            interpreter_path=runtime["interpreter_path"],
        )
    except (TypeError, ValueError):
        raise StageReceiptError("stage 回执发布运行身份无效") from None
    if not isinstance(protected, dict) or set(protected) != set(_PROTECTED_UNITS):
        raise StageReceiptError("stage 回执受保护 unit 摘要无效")
    if any(
        type(protected[name]) is not str or _SHA256.fullmatch(protected[name]) is None
        for name in _PROTECTED_UNITS
    ):
        raise StageReceiptError("stage 回执受保护 unit 摘要无效")
    if type(enabled) is not list or tuple(enabled) != _REQUIRED_ENABLED_UNITS:
        raise StageReceiptError("stage 回执延迟单元启用清单无效")
    normalized = {
        "schema": _SCHEMA_VERSION,
        "runtime": _runtime_mapping(release),
        "protected_units": {name: protected[name] for name in _PROTECTED_UNITS},
        "enabled_units": list(_REQUIRED_ENABLED_UNITS),
    }
    if content != _canonical_json(normalized) + b"\n":
        raise StageReceiptError("stage 回执不是规范编码")
    return normalized


def _prove_enabled_units(probe: UnitEnabledProbe | None) -> None:
    selected = default_unit_enabled_probe if probe is None else probe
    if not callable(selected):
        raise StageReceiptError("stage 延迟单元启用状态证明不可用")
    for name in _REQUIRED_ENABLED_UNITS:
        try:
            enabled = selected(name)
        except (KeyboardInterrupt, SystemExit, MemoryError):
            raise
        except Exception:
            raise StageReceiptError("stage 延迟单元启用状态无法证明") from None
        if enabled is not True:
            raise StageReceiptError("stage 延迟单元启用状态未证明")


def default_unit_enabled_probe(name: str) -> bool:
    """通过生产 systemctl 适配器复读唯一明确的 enabled 状态。"""
    from codev_platform.mcp_systemd_install_systemd import default_unit_state_reader

    return default_unit_state_reader(name).unit_file_state == "enabled"


def _canonical_json(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


__all__ = [
    "STAGE_RECEIPT_PATH",
    "StageReceiptError",
    "build_stage_receipt_content",
    "default_stage_receipt_restorer",
    "default_stage_receipt_snapshot_reader",
    "default_stage_receipt_verifier",
    "default_stage_receipt_writer",
    "prove_staged_systemd_payload",
    "read_stage_receipt_expected_digests",
    "verify_stage_payload_runtime_identity",
    "verify_stage_receipt_content",
]
