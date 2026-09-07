"""current runtime 访问投影修复的维护窗口 CLI 门面。"""

from __future__ import annotations

import dataclasses
import json
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codev_platform.runtime_current_access_repair import (
    RuntimeCurrentAccessRepairProof,
    repair_current_runtime_service_access,
)
from codev_platform.runtime_service_process import ServiceAccount


class RuntimeAccessRepairCommandError(RuntimeError):
    """CLI 无法在受控维护窗口内执行访问投影修复。"""


@dataclass(frozen=True, slots=True)
class _CommandPorts:
    load_config: Callable[[], dict[str, Any]]
    release_root: Callable[[dict[str, Any] | None, Path | None], Path]
    resolve_service_account: Callable[[str], ServiceAccount]
    maintenance_permit: Callable[[], AbstractContextManager[bool]]
    inspect_maintenance: Callable[[], None]
    repair: Callable[..., RuntimeCurrentAccessRepairProof]


def cmd_runtime_access_repair(args: object) -> int:
    """默认 dry-run；显式 ``--yes`` 才在维护窗口内提交访问投影变更。"""
    sys.dont_write_bytecode = True
    try:
        ports = _default_ports()
        runtime_root = _runtime_root(args, ports)
        account = ports.resolve_service_account(_service_user(args))
        proof = _repair_with_gate(
            runtime_root,
            account=account,
            write=_write_requested(args),
            ports=ports,
        )
        _emit(
            {
                "kind": "runtime_access_repair",
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


def _repair_with_gate(
    root: Path,
    *,
    account: ServiceAccount,
    write: bool,
    ports: _CommandPorts,
) -> RuntimeCurrentAccessRepairProof:
    if type(account) is not ServiceAccount or type(write) is not bool:
        raise RuntimeAccessRepairCommandError("运行时访问修复命令参数无效")
    if not write:
        return _require_proof(ports.repair(root, account=account, dry_run=True))
    with ports.maintenance_permit() as permitted:
        if permitted is not True:
            raise RuntimeAccessRepairCommandError("reindex 维护窗口未授权")
        ports.inspect_maintenance()
        proof = _require_proof(ports.repair(root, account=account, dry_run=False))
        ports.inspect_maintenance()
        return proof


def _runtime_root(args: object, ports: _CommandPorts) -> Path:
    from codev_platform.ops.runtime import _runtime_root as resolve_runtime_root

    return resolve_runtime_root(args, ports)


def _service_user(args: object) -> str:
    value = getattr(args, "service_user", None)
    if type(value) is not str or not value:
        raise RuntimeAccessRepairCommandError("服务账号参数无效")
    return value


def _write_requested(args: object) -> bool:
    value = getattr(args, "yes", False)
    if type(value) is not bool:
        raise RuntimeAccessRepairCommandError("确认参数无效")
    return value


def _require_proof(value: object) -> RuntimeCurrentAccessRepairProof:
    if type(value) is not RuntimeCurrentAccessRepairProof:
        raise RuntimeAccessRepairCommandError("运行时访问修复证明无效")
    return value


def _default_ports() -> _CommandPorts:
    from codev_platform.core.config import load_config
    from codev_platform.ops.reindex_maintenance import inspect_reindex_maintenance
    from codev_platform.reindex.maintenance_gate_state import maintenance_admin_window_permit
    from codev_platform.runtime_release import release_root
    from codev_platform.runtime_service_process import resolve_service_account

    return _CommandPorts(
        load_config=load_config,
        release_root=release_root,
        resolve_service_account=resolve_service_account,
        maintenance_permit=maintenance_admin_window_permit,
        inspect_maintenance=inspect_reindex_maintenance,
        repair=repair_current_runtime_service_access,
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
            "error": "runtime_access_repair_failed",
            "kind": "runtime_access_repair",
            "status": "error",
        },
        error=True,
    )


__all__ = ["cmd_runtime_access_repair"]
