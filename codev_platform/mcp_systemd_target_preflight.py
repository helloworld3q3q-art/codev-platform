"""在 systemd 目标用户上下文中执行并核验运行时预检证明。"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from dataclasses import replace
from pathlib import Path

from codev_platform.mcp_systemd_install_contract import (
    SystemdInstallManifest,
    SystemdInstallTransactionError,
    SystemdRuntimeBinding,
    SystemdUnitPayload,
)
from codev_platform.mcp_systemd_effective_payload import parse_unit_exec_start
from codev_platform.mcp_systemd_runtime import posix_runtime_environment
from codev_platform.mcp_systemd_unit_registry import (
    CODEGRAPH_SYSTEMD_UNIT_NAME,
    MANAGED_SYSTEMD_UNIT_NAMES,
    REINDEX_SYSTEMD_UNIT_NAME,
    RUNTIME_BOUND_SYSTEMD_UNIT_NAMES,
)
from codev_platform.runtime_service_process import resolve_service_account
from codev_platform.runtime_preflight_contract import DEFAULT_PROBE_TIMEOUT_SEC


_SYSTEMD_RUN = "/usr/bin/systemd-run"
_PROOF_MODULE = "codev_platform.runtime_preflight_proof"
_PROCESS_GRACE_SEC = 10.0
_MAX_PROOF_BYTES = 64 * 1024
_TERMINATION_EXCEPTIONS = (KeyboardInterrupt, SystemExit, MemoryError)
_LEGACY_CURRENT_ALIAS_UNITS = frozenset(
    {
        CODEGRAPH_SYSTEMD_UNIT_NAME,
        REINDEX_SYSTEMD_UNIT_NAME,
    }
)
_LEGACY_WORKING_DIRECTORY = b"WorkingDirectory=%h\n"


def verify_target_user_systemd_preflight(
    manifest: SystemdInstallManifest,
    payloads: tuple[SystemdUnitPayload, ...],
    bound: object,
) -> None:
    """在不可变 release 上运行瞬时探针，并与锁内冻结载荷逐项比对。"""
    binding = _require_locked_binding(manifest, payloads, bound)
    working_directory = _target_user_working_directory(binding.target_user)
    command = _build_transient_command(manifest, binding, working_directory)
    output = _execute_transient(
        command,
        float(DEFAULT_PROBE_TIMEOUT_SEC) + _PROCESS_GRACE_SEC,
    )
    actual = _parse_canonical_proof(output)
    expected = _expected_proof(manifest, payloads, binding)
    if actual == expected:
        return
    if _matches_legacy_proof(
        actual,
        manifest,
        payloads,
        binding,
        working_directory,
    ):
        return
    raise SystemdInstallTransactionError("systemd 目标用户预检证明不一致")


def _require_locked_binding(
    manifest: object,
    payloads: object,
    bound: object,
) -> SystemdRuntimeBinding:
    if type(manifest) is not SystemdInstallManifest or type(payloads) is not tuple:
        raise SystemdInstallTransactionError("systemd 目标用户预检输入无效")
    if not all(type(payload) is SystemdUnitPayload for payload in payloads):
        raise SystemdInstallTransactionError("systemd 目标用户预检载荷无效")
    binding = manifest.runtime_binding
    if type(binding) is not SystemdRuntimeBinding:
        raise SystemdInstallTransactionError("systemd 目标用户预检缺少运行时绑定")
    try:
        root = Path(bound.root).as_posix()  # type: ignore[attr-defined]
        release_id = bound.release_id  # type: ignore[attr-defined]
        runtime_revision = bound.runtime_revision  # type: ignore[attr-defined]
        interpreter = Path(bound.interpreter_path).as_posix()  # type: ignore[attr-defined]
    except _TERMINATION_EXCEPTIONS:
        raise
    except Exception:
        raise SystemdInstallTransactionError("systemd 目标用户预检运行时绑定无效") from None
    if (
        root != binding.release_root
        or release_id != binding.expected_release_id
        or runtime_revision != manifest.runtime_revision
        or interpreter != binding.immutable_python.as_posix()
    ):
        raise SystemdInstallTransactionError("systemd 目标用户预检运行时绑定不一致")
    return binding


def _build_transient_command(
    manifest: SystemdInstallManifest,
    binding: SystemdRuntimeBinding,
    working_directory: str,
) -> tuple[str, ...]:
    runtime_limit = int(math.ceil(float(DEFAULT_PROBE_TIMEOUT_SEC)))
    command = [
        _SYSTEMD_RUN,
        "--quiet",
        "--wait",
        "--pipe",
        "--collect",
        f"--uid={binding.target_user}",
        "--service-type=exec",
        f"--property=RuntimeMaxSec={runtime_limit}s",
        "--property=UMask=0077",
    ]
    if binding.environment_file is not None:
        command.append(f"--property=EnvironmentFile={binding.environment_file}")
    command.extend(f"--setenv={item}" for item in posix_runtime_environment(binding.release_root))
    command.extend(
        (
            f"--property=WorkingDirectory={working_directory}",
            binding.immutable_python.as_posix(),
            "-I",
            "-m",
            _PROOF_MODULE,
            "--release-root",
            binding.release_root,
            "--expected-release-id",
            binding.expected_release_id,
            "--expected-runtime-revision",
            manifest.runtime_revision,
            "--target-user",
            binding.target_user,
        )
    )
    return tuple(command)


def _matches_legacy_proof(
    actual: object,
    manifest: SystemdInstallManifest,
    payloads: tuple[SystemdUnitPayload, ...],
    binding: SystemdRuntimeBinding,
    working_directory: str,
) -> bool:
    """只接受冻结 release 已证实的两种历史渲染差异组合。"""
    try:
        if actual == _legacy_current_alias_proof(manifest, payloads, binding):
            return True
        combined = _legacy_current_alias_percent_home_proof(
            manifest,
            payloads,
            binding,
            working_directory,
        )
    except SystemdInstallTransactionError:
        return False
    return actual == combined


def _target_user_working_directory(target_user: str) -> str:
    """从既有服务账号真值取得 transient unit 可解析的绝对主目录。"""
    try:
        account = resolve_service_account(target_user)
        if account.name != target_user:
            raise ValueError("目标账号记录漂移")
        working_directory = account.home.as_posix()
    except _TERMINATION_EXCEPTIONS:
        raise
    except Exception:
        raise SystemdInstallTransactionError("systemd 目标用户主目录无效") from None
    return working_directory


def _execute_transient(command: tuple[str, ...], timeout_sec: float) -> bytes:
    """不经 shell 执行瞬时服务；错误正文和 stderr 均不进入上层消息。"""
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout_sec,
        )
    except _TERMINATION_EXCEPTIONS:
        raise
    except subprocess.TimeoutExpired:
        raise SystemdInstallTransactionError("systemd 目标用户预检执行超时") from None
    except (OSError, subprocess.SubprocessError):
        raise SystemdInstallTransactionError("systemd 目标用户预检无法执行") from None
    if completed.returncode != 0:
        raise SystemdInstallTransactionError("systemd 目标用户预检执行失败")
    if type(completed.stdout) is not bytes:
        raise SystemdInstallTransactionError("systemd 目标用户预检输出无效")
    return completed.stdout


def _parse_canonical_proof(output: bytes) -> object:
    if type(output) is not bytes or not output or len(output) > _MAX_PROOF_BYTES:
        raise SystemdInstallTransactionError("systemd 目标用户预检输出无效")
    try:
        text = output.decode("ascii")
        payload = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
        canonical = (
            json.dumps(
                payload,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
    except _TERMINATION_EXCEPTIONS:
        raise
    except Exception:
        raise SystemdInstallTransactionError("systemd 目标用户预检输出无效") from None
    if text != canonical:
        raise SystemdInstallTransactionError("systemd 目标用户预检输出无效")
    return payload


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 包含重复字段")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> object:
    raise ValueError("JSON 包含非有限常量")


def _expected_proof(
    manifest: SystemdInstallManifest,
    payloads: tuple[SystemdUnitPayload, ...],
    binding: SystemdRuntimeBinding,
) -> dict[str, object]:
    units = [
        {
            "name": payload.spec.unit_name,
            "sha256": hashlib.sha256(payload.content).hexdigest(),
        }
        for payload in sorted(payloads, key=lambda item: item.spec.unit_name)
    ]
    return {
        "release_id": binding.expected_release_id,
        "runtime_revision": manifest.runtime_revision,
        "schema_version": 1,
        "target_user": binding.target_user,
        "units": units,
    }


def _legacy_current_alias_proof(
    manifest: SystemdInstallManifest,
    payloads: tuple[SystemdUnitPayload, ...],
    binding: SystemdRuntimeBinding,
) -> dict[str, object]:
    """仅兼容旧 release 对两个受保护 unit 的固定 current 解释器摘要。"""
    immutable = binding.immutable_python.as_posix().encode("ascii")
    current = f"{binding.release_root}/current/venv/bin/python".encode("ascii")
    names = {payload.spec.unit_name for payload in payloads}
    if not _LEGACY_CURRENT_ALIAS_UNITS.issubset(names) or immutable == current:
        raise SystemdInstallTransactionError("systemd 目标用户预检证明不一致")
    units = []
    for payload in sorted(payloads, key=lambda item: item.spec.unit_name):
        content = payload.content
        if payload.spec.unit_name in _LEGACY_CURRENT_ALIAS_UNITS:
            if (
                not _protected_exec_start_uses_immutable(payload, binding)
                or content.count(immutable) != 1
            ):
                raise SystemdInstallTransactionError("systemd 目标用户预检证明不一致")
            content = content.replace(immutable, current)
        units.append(
            {
                "name": payload.spec.unit_name,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    return {
        "release_id": binding.expected_release_id,
        "runtime_revision": manifest.runtime_revision,
        "schema_version": 1,
        "target_user": binding.target_user,
        "units": units,
    }


def _legacy_current_alias_percent_home_proof(
    manifest: SystemdInstallManifest,
    payloads: tuple[SystemdUnitPayload, ...],
    binding: SystemdRuntimeBinding,
    working_directory: str,
) -> dict[str, object]:
    """兼容冻结 release 同时使用 current 与 %h 的唯一历史摘要。"""
    return _legacy_current_alias_proof(
        manifest,
        _legacy_percent_home_payloads(payloads, working_directory),
        binding,
    )


def _legacy_percent_home_payloads(
    payloads: tuple[SystemdUnitPayload, ...],
    working_directory: str,
) -> tuple[SystemdUnitPayload, ...]:
    """仅将完整运行时 unit 的单一显式 home 还原为旧 %h。"""
    names = tuple(payload.spec.unit_name for payload in payloads)
    if len(names) != len(set(names)) or frozenset(names) != MANAGED_SYSTEMD_UNIT_NAMES:
        raise SystemdInstallTransactionError("systemd 目标用户预检证明不一致")
    try:
        explicit = f"WorkingDirectory={working_directory}\n".encode()
    except _TERMINATION_EXCEPTIONS:
        raise
    except Exception:
        raise SystemdInstallTransactionError("systemd 目标用户预检证明不一致") from None
    if not explicit or explicit == _LEGACY_WORKING_DIRECTORY:
        raise SystemdInstallTransactionError("systemd 目标用户预检证明不一致")
    transformed: list[SystemdUnitPayload] = []
    for payload in payloads:
        name = payload.spec.unit_name
        content = payload.content
        if name in RUNTIME_BOUND_SYSTEMD_UNIT_NAMES:
            if content.count(explicit) != 1 or _LEGACY_WORKING_DIRECTORY in content:
                raise SystemdInstallTransactionError("systemd 目标用户预检证明不一致")
            content = content.replace(explicit, _LEGACY_WORKING_DIRECTORY)
        elif explicit in content or _LEGACY_WORKING_DIRECTORY in content:
            raise SystemdInstallTransactionError("systemd 目标用户预检证明不一致")
        transformed.append(
            SystemdUnitPayload(
                spec=replace(
                    payload.spec,
                    content_digest=hashlib.sha256(content).hexdigest(),
                ),
                content=content,
            )
        )
    return tuple(transformed)


def _protected_exec_start_uses_immutable(
    payload: SystemdUnitPayload,
    binding: SystemdRuntimeBinding,
) -> bool:
    try:
        argv = parse_unit_exec_start(payload)
    except _TERMINATION_EXCEPTIONS:
        raise
    except Exception:
        return False
    return argv is not None and argv[0] == binding.immutable_python.as_posix()


__all__ = ["verify_target_user_systemd_preflight"]
