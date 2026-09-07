"""暂存 release 服务访问发布的 CLI 门面。"""

from __future__ import annotations

import dataclasses
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codev_platform.runtime_service_process import ServiceAccount
from codev_platform.runtime_staged_release_access import (
    RuntimeStagedReleaseAccessProof,
    publish_staged_release_service_access,
)


class RuntimeStagedReleaseAccessCommandError(RuntimeError):
    """CLI 无法安全发布暂存 release 的服务访问。"""


@dataclass(frozen=True, slots=True)
class _CommandPorts:
    load_config: Callable[[], dict[str, Any]]
    release_root: Callable[[dict[str, Any] | None, Path | None], Path]
    resolve_service_account: Callable[[str], ServiceAccount]
    publish: Callable[..., RuntimeStagedReleaseAccessProof]


def cmd_runtime_staged_release_access(args: object) -> int:
    """默认只做静态 dry-run，显式确认后才发布服务组访问投影。"""
    sys.dont_write_bytecode = True
    try:
        ports = _default_ports()
        root = _runtime_root(args, ports)
        account = ports.resolve_service_account(_service_user(args))
        proof = _require_proof(
            ports.publish(
                root,
                account=account,
                release_id=_release_id(args),
                dry_run=not _write_requested(args),
            )
        )
        _emit(
            {
                "kind": "runtime_staged_release_access",
                "result": dataclasses.asdict(proof),
                "status": "ok",
            }
        )
        return 0
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        _emit_failure()
        return 1


def _runtime_root(args: object, ports: _CommandPorts) -> Path:
    from codev_platform.ops.runtime import _runtime_root as resolve_runtime_root

    return resolve_runtime_root(args, ports)


def _service_user(args: object) -> str:
    value = getattr(args, "service_user", None)
    if type(value) is not str or not value:
        raise RuntimeStagedReleaseAccessCommandError("服务账号参数无效")
    return value


def _release_id(args: object) -> str:
    value = getattr(args, "release_id", None)
    if type(value) is not str or not value:
        raise RuntimeStagedReleaseAccessCommandError("暂存 release 参数无效")
    return value


def _write_requested(args: object) -> bool:
    value = getattr(args, "yes", False)
    if type(value) is not bool:
        raise RuntimeStagedReleaseAccessCommandError("确认参数无效")
    return value


def _require_proof(value: object) -> RuntimeStagedReleaseAccessProof:
    if type(value) is not RuntimeStagedReleaseAccessProof:
        raise RuntimeStagedReleaseAccessCommandError("暂存 release 服务访问证明无效")
    return value


def _default_ports() -> _CommandPorts:
    from codev_platform.core.config import load_config
    from codev_platform.runtime_release import release_root
    from codev_platform.runtime_service_process import resolve_service_account

    return _CommandPorts(
        load_config=load_config,
        release_root=release_root,
        resolve_service_account=resolve_service_account,
        publish=publish_staged_release_service_access,
    )


def _emit(payload: dict[str, object], *, error: bool = False) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        file=sys.stderr if error else sys.stdout,
        flush=True,
    )


def _emit_failure() -> None:
    _emit(
        {
            "error": "runtime_staged_release_access_failed",
            "kind": "runtime_staged_release_access",
            "status": "error",
        },
        error=True,
    )


__all__ = ["cmd_runtime_staged_release_access"]
