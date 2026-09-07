"""受管机器配置 bootstrap 的无秘密 CLI 门面。"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable

from codev_platform.core.runtime_models import require_sha256
from codev_platform.runtime_managed_configuration import ManagedConfigurationBootstrapReceipt


BootstrapRunner = Callable[..., ManagedConfigurationBootstrapReceipt]


def cmd_runtime_managed_config_bootstrap(args: object) -> int:
    """默认 dry-run；仅 ``--yes`` 在已证明维护窗口内写入缺失配置。"""
    sys.dont_write_bytecode = True
    try:
        receipt = _runner()(
            service_user=_service_user(args),
            apply=_write_requested(args),
        )
        result = _public_receipt(receipt)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        _emit_failure()
        return 1
    _emit(
        {
            "kind": "runtime_managed_config_bootstrap",
            "result": result,
            "status": "ok",
        }
    )
    return 0


def _runner() -> BootstrapRunner:
    from codev_platform.runtime_managed_configuration_bootstrap import (
        bootstrap_service_managed_configuration,
    )

    return bootstrap_service_managed_configuration


def _service_user(args: object) -> str:
    value = getattr(args, "service_user", None)
    if type(value) is not str or not value:
        raise ValueError("服务账号参数无效")
    return value


def _write_requested(args: object) -> bool:
    value = getattr(args, "yes", False)
    if type(value) is not bool:
        raise ValueError("确认参数无效")
    return value


def _public_receipt(value: object) -> dict[str, str]:
    if type(value) is not ManagedConfigurationBootstrapReceipt or value.state not in {
        "ready",
        "published",
        "already_published",
    }:
        raise TypeError("受管配置引导回执无效")
    try:
        return {
            "config_sha256": require_sha256(value.config_sha256, field="config_sha256"),
            "environment_sha256": require_sha256(
                value.environment_sha256,
                field="environment_sha256",
            ),
            "evidence_sha256": require_sha256(value.evidence_sha256, field="evidence_sha256"),
            "state": value.state,
        }
    except ValueError:
        raise TypeError("受管配置引导回执无效") from None


def _emit(payload: dict[str, object], *, error: bool = False) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        file=sys.stderr if error else sys.stdout,
        flush=True,
    )


def _emit_failure() -> None:
    _emit(
        {
            "error": "runtime_managed_config_bootstrap_failed",
            "kind": "runtime_managed_config_bootstrap",
            "status": "error",
        },
        error=True,
    )


__all__ = ["cmd_runtime_managed_config_bootstrap"]
