"""日常薄发布的 Linux/systemd 生产适配器。"""

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

from codev_platform.core.config import load_config
from codev_platform.core.runtime_models import SystemdRuntime, require_sha256
from codev_platform.mcp_systemd_unit_registry import WEBHOOK_SYSTEMD_UNIT_NAME
from codev_platform.runtime_deployment_contract import RuntimeDeploymentError
from codev_platform.runtime_service_process import ServiceAccount, resolve_service_account
from codev_platform.runtime_systemd_acceptance import (
    verify_ingress_stability,
    verify_systemd_stability,
)
from codev_platform.runtime_thin_promotion import (
    DAILY_RUNTIME_SERVICE_UNITS,
    ThinPromotionError,
    ThinPromotionPorts,
)


_EXPECTED_MCP_SERVICE_NAMES = (
    "agent-memory",
    "codegraph",
    "graph",
    "platform-docs",
)
_MCP_READINESS_TIMEOUT_SEC = 90.0
_MCP_READINESS_POLL_INTERVAL_SEC = 2.0


def production_promotion_ports(
    runtime_root: Path,
    *,
    service_user: str,
) -> ThinPromotionPorts:
    """装配唯一正式环境的受控发布端口，不读取或回显任何 secret。"""
    _require_linux_root()
    account = _service_account(service_user)
    config = _service_config(account)
    root = _runtime_root(runtime_root)
    return ThinPromotionPorts(
        promotion_lock=lambda: _daily_promotion_lock(root),
        preflight=_preflight_normal_runtime,
        publish_target_service_access=lambda release_id: _publish_target_service_access(
            root,
            account,
            release_id,
        ),
        activate=_activate_expected_current,
        rollback=_rollback_expected_current,
        install_current_units=lambda: _install_current_release_units(root, account, config),
        restart_runtime_services=_restart_runtime_services,
        verify_runtime_services=_verify_runtime_services,
        verify_mcp_services=lambda: _verify_mcp_services(config),
        verify_ingress=lambda: _verify_ingress(config),
    )


@contextmanager
def _daily_promotion_lock(runtime_root: Path) -> Iterator[None]:
    """串行维护转换与 runtime 指针，覆盖前置校验至服务验收的完整窗口。"""
    from codev_platform.reindex.maintenance_gate import maintenance_systemd_transition_session
    from codev_platform.runtime_storage import deployment_lock

    with maintenance_systemd_transition_session():
        with deployment_lock(runtime_root):
            yield


def _preflight_normal_runtime() -> None:
    """日常薄发布只接受未进入维护窗口的完整常态。"""
    try:
        from codev_platform.mcp_systemd_install_systemd import default_runtime_mask_proof
        from codev_platform.reindex.maintenance_gate import maintenance_gate_active

        if maintenance_gate_active() is not False:
            raise RuntimeError("reindex 维护门禁仍在生效")
        default_runtime_mask_proof()
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise ThinPromotionError("日常薄发布只允许常态运行时") from None


def _activate_expected_current(root: Path, target: str, rollback: str):
    from codev_platform.runtime_release import activate_release_from_expected_current

    return activate_release_from_expected_current(root, target, rollback)


def _publish_target_service_access(
    runtime_root: Path,
    account: ServiceAccount,
    release_id: str,
) -> None:
    """在切换 current 前发布目标对象，并以目标账号探针作为完成证明。"""
    try:
        from codev_platform.runtime_staged_release_access import (
            RuntimeStagedReleaseAccessProof,
            publish_staged_release_service_access,
        )

        proof = publish_staged_release_service_access(
            runtime_root,
            account=account,
            release_id=release_id,
            dry_run=False,
        )
        if (
            type(proof) is not RuntimeStagedReleaseAccessProof
            or proof.dry_run is not False
            or proof.release_id != release_id
            or proof.service_uid != account.uid
            or proof.service_gid != account.gid
            or type(proof.target_user_evidence_sha256) is not str
        ):
            raise ValueError("目标服务访问回执无效")
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise ThinPromotionError("日常薄发布目标服务访问发布失败") from None


def _rollback_expected_current(root: Path, target: str, rollback: str):
    from codev_platform.runtime_release import rollback_release_from_expected_current

    return rollback_release_from_expected_current(root, target, rollback)


def _install_current_release_units(
    runtime_root: Path,
    account: ServiceAccount,
    config: dict[str, Any],
) -> None:
    """通过既有 install-only 事务刷新受管 unit，不自行写入 /etc。"""
    try:
        from codev_platform import mcp_systemd
        from codev_platform.mcp_systemd_install_transaction import (
            install_systemd_install_only_from_manifest_path,
        )

        package = mcp_systemd.install_systemd(
            config,
            account.name,
            runtime=SystemdRuntime(runtime_root),
            no_restart=True,
        )
        manifest = _manifest_path(package)
        install_systemd_install_only_from_manifest_path(manifest)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise ThinPromotionError("日常薄发布 systemd unit 刷新失败") from None


def _manifest_path(package: object) -> Path:
    """只接受既有安装器生成的固定 install-only 命令形状。"""
    if type(package) is not dict:
        raise ThinPromotionError("日常薄发布 systemd 安装包无效")
    command = package.get("transaction_argv")
    if type(command) is not tuple or command.count("--manifest") != 1:
        raise ThinPromotionError("日常薄发布 systemd 安装包无效")
    index = command.index("--manifest")
    if (
        index + 2 >= len(command)
        or command[index + 2] != "--install-only"
        or command[-1] != "--install-only"
        or not all(type(item) is str and item for item in command)
    ):
        raise ThinPromotionError("日常薄发布 systemd 安装包无效")
    raw_path = command[index + 1]
    posix_path = PurePosixPath(raw_path)
    if not posix_path.is_absolute() or posix_path.as_posix() != raw_path or ".." in posix_path.parts:
        raise ThinPromotionError("日常薄发布 systemd 安装包无效")
    return Path(raw_path)


def _restart_runtime_services() -> None:
    try:
        from codev_platform.mcp_systemd_systemctl import systemctl

        systemctl(("systemctl", "restart", *DAILY_RUNTIME_SERVICE_UNITS))
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise ThinPromotionError("日常薄发布服务重启失败") from None


def _verify_runtime_services() -> str:
    try:
        from codev_platform.mcp_systemd_systemctl import verify_running_units

        verify_running_units(DAILY_RUNTIME_SERVICE_UNITS)
        evidence = verify_systemd_stability().evidence_sha256
        return _evidence(evidence, "systemd")
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except ThinPromotionError:
        raise
    except Exception:
        raise ThinPromotionError("日常薄发布服务稳定性无法证明") from None


def _verify_mcp_services(config: dict[str, Any]) -> str:
    """以严格 HTTP 同轮探测四个固定 MCP，不携带 Bearer token。"""
    try:
        from codev_platform.mcp_source_client import wait_until_source_serving

        rows = wait_until_source_serving(
            config,
            "local",
            timeout=_MCP_READINESS_TIMEOUT_SEC,
            interval=_MCP_READINESS_POLL_INTERVAL_SEC,
        )
        _verify_expected_mcp_service_rows(rows)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise ThinPromotionError("日常薄发布 MCP 健康验收失败") from None
    return hashlib.sha256(
        "".join(f"{name}:ok\n" for name in _EXPECTED_MCP_SERVICE_NAMES).encode("ascii")
    ).hexdigest()


def _verify_expected_mcp_service_rows(rows: object) -> None:
    """验证发布验收的固定端点集合，拒绝以等待掩盖拓扑或状态漂移。"""
    if type(rows) is not list:
        raise ValueError("MCP 探针结果无效")
    indexed: dict[str, str] = {}
    for row in rows:
        if type(row) is not dict:
            raise ValueError("MCP 探针结果无效")
        name = row.get("name")
        status = row.get("status")
        if type(name) is not str or type(status) is not str or name in indexed:
            raise ValueError("MCP 探针结果无效")
        indexed[name] = status
    if tuple(sorted(indexed)) != _EXPECTED_MCP_SERVICE_NAMES:
        raise ValueError("MCP 集合漂移")
    if any(indexed[name] != "ok" for name in _EXPECTED_MCP_SERVICE_NAMES):
        raise ValueError("MCP 健康探针失败")


def _verify_ingress(config: dict[str, Any]) -> str:
    try:
        from codev_platform.runtime_webhook_acceptance import verify_webhook_http

        systemd_evidence = _evidence(verify_ingress_stability().evidence_sha256, "入口 systemd")
        webhook_evidence = _evidence(verify_webhook_http(config).evidence_sha256, "Webhook")
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except ThinPromotionError:
        raise
    except Exception:
        raise ThinPromotionError("日常薄发布入口验收失败") from None
    return hashlib.sha256(f"{systemd_evidence}\n{webhook_evidence}\n".encode("ascii")).hexdigest()


def _service_account(value: object) -> ServiceAccount:
    try:
        account = resolve_service_account(value)  # type: ignore[arg-type]
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise ThinPromotionError("日常薄发布服务账号无效") from None
    if type(account) is not ServiceAccount:
        raise ThinPromotionError("日常薄发布服务账号无效")
    return account


def _service_config(account: ServiceAccount) -> dict[str, Any]:
    try:
        config = load_config(home=account.home)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise ThinPromotionError("日常薄发布服务配置不可用") from None
    if type(config) is not dict:
        raise ThinPromotionError("日常薄发布服务配置不可用")
    return config


def _runtime_root(value: object) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise ThinPromotionError("日常薄发布运行时根无效")
    return value


def _require_linux_root() -> None:
    if (
        os.name != "posix"
        or not sys.platform.startswith("linux")
        or not hasattr(os, "geteuid")
        or os.geteuid() != 0
    ):
        raise ThinPromotionError("日常薄发布只允许 Linux root 执行")


def _evidence(value: object, label: str) -> str:
    try:
        return require_sha256(value, field=f"{label}_evidence")
    except ValueError:
        raise ThinPromotionError("日常薄发布验收证据无效") from None


if len(DAILY_RUNTIME_SERVICE_UNITS) != len(set(DAILY_RUNTIME_SERVICE_UNITS)) or (
    DAILY_RUNTIME_SERVICE_UNITS[-1] != WEBHOOK_SYSTEMD_UNIT_NAME
):
    raise RuntimeDeploymentError("日常薄发布服务集合无效")


__all__ = ["production_promotion_ports"]
