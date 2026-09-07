"""跨重启部署停写门禁及固定 systemd 条件 drop-in。"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from codev_platform.core.systemd_maintenance_contract import (
    CODEGRAPH_MAINTENANCE_GUARD_DROPIN_CONTENT,
    CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH,
)
from codev_platform.ops.reindex_codegraph_resume_managed_path import (
    RootOwnedRegularFileSnapshot,
    TrustedManagedPathError,
    read_optional_root_owned_regular_file_snapshot,
    remove_root_owned_regular_file,
    write_root_owned_regular_file_atomic,
)
from codev_platform.ops.systemd_condition_guard import (
    SystemdConditionGuardError,
    SystemdConditionSpec,
    verify_systemd_condition_guard,
)
from codev_platform.mcp_systemd_unit_registry import DEPLOYMENT_GUARDED_SYSTEMD_UNITS
from codev_platform.runtime_deployment_contract import DeploymentPlan, RuntimeDeploymentError
from codev_platform.runtime_systemd_gate_contract import (
    DEPLOYMENT_GUARD_DROP_IN_CONTENT,
    DEPLOYMENT_GUARD_MARKER_PATH,
    INGRESS_GATE_DROP_IN_CONTENT,
    deployment_guard_drop_in_path,
    ingress_gate_drop_in_path,
)


# 兼容既有部署守卫公共名称；成员分类唯一真值位于受管 unit 注册表。
GUARDED_SYSTEMD_UNITS = DEPLOYMENT_GUARDED_SYSTEMD_UNITS
_MAX_GUARD_BYTES = 4096


@dataclass(frozen=True, slots=True)
class DeploymentGuardEvidence:
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class DeploymentGuardPorts:
    install_drop_in: Callable[[str], None]
    verify_drop_in: Callable[[str], bool]
    reload_systemd: Callable[[], None]
    write_guard: Callable[[bytes], None]
    read_guard: Callable[[], bytes | None]
    delete_guard: Callable[[], None]

    def validate(self) -> DeploymentGuardPorts:
        if type(self) is not DeploymentGuardPorts or not all(
            callable(item)
            for item in (
                self.install_drop_in,
                self.verify_drop_in,
                self.reload_systemd,
                self.write_guard,
                self.read_guard,
                self.delete_guard,
            )
        ):
            raise RuntimeDeploymentError("部署停写门禁端口不可用")
        return self


def deployment_guard_payload(plan: DeploymentPlan) -> bytes:
    """门禁只记录计划摘要和目标提交，不保存配置路径正文或秘密。"""
    if type(plan) is not DeploymentPlan:
        raise RuntimeDeploymentError("部署停写门禁计划无效")
    return (
        json.dumps(
            {
                "plan_sha256": plan.digest,
                "schema_version": 1,
                "target_revision": plan.target_revision,
            },
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")


def activate_deployment_guard(
    plan: DeploymentPlan,
    *,
    ports: DeploymentGuardPorts | None = None,
    platform_name: str | None = None,
) -> DeploymentGuardEvidence:
    """先让所有受管 unit 具备跨重启条件，再原子创建门禁。"""
    _require_linux(platform_name)
    active = default_ports(plan) if ports is None else ports.validate()
    try:
        for unit in GUARDED_SYSTEMD_UNITS:
            active.install_drop_in(unit)
        active.reload_systemd()
        for unit in GUARDED_SYSTEMD_UNITS:
            if active.verify_drop_in(unit) is not True:
                raise RuntimeDeploymentError("部署停写 drop-in 无法证明")
        payload = deployment_guard_payload(plan)
        active.write_guard(payload)
        if active.read_guard() != payload:
            raise RuntimeDeploymentError("部署停写门禁身份不一致")
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RuntimeDeploymentError:
        raise
    except Exception:
        raise RuntimeDeploymentError("部署停写门禁激活失败") from None
    return _evidence(payload)


def verify_deployment_guard_active(
    plan: DeploymentPlan,
    *,
    ports: DeploymentGuardPorts | None = None,
    platform_name: str | None = None,
) -> DeploymentGuardEvidence:
    """续跑前同时复验全部 drop-in 与计划绑定的门禁内容。"""
    _require_linux(platform_name)
    active = default_ports(plan) if ports is None else ports.validate()
    try:
        if any(active.verify_drop_in(unit) is not True for unit in GUARDED_SYSTEMD_UNITS):
            raise RuntimeDeploymentError("部署停写 drop-in 无法证明")
        payload = deployment_guard_payload(plan)
        if active.read_guard() != payload:
            raise RuntimeDeploymentError("部署停写门禁身份不一致")
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RuntimeDeploymentError:
        raise
    except Exception:
        raise RuntimeDeploymentError("部署停写门禁无法证明") from None
    return _evidence(payload)


def deactivate_deployment_guard(
    plan: DeploymentPlan,
    *,
    ports: DeploymentGuardPorts | None = None,
    platform_name: str | None = None,
) -> DeploymentGuardEvidence:
    """仅在调用方已安装 target 载荷后删除门禁，并同步目录元数据。"""
    _require_linux(platform_name)
    active = default_ports(plan) if ports is None else ports.validate()
    evidence = verify_deployment_guard_active(
        plan,
        ports=active,
        platform_name=platform_name,
    )
    try:
        active.delete_guard()
        if active.read_guard() is not None:
            raise RuntimeDeploymentError("部署停写门禁删除后仍存在")
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RuntimeDeploymentError:
        raise
    except Exception:
        raise RuntimeDeploymentError("部署停写门禁无法安全删除") from None
    return evidence


def verify_deployment_guard_inactive(
    plan: DeploymentPlan,
    *,
    ports: DeploymentGuardPorts | None = None,
    platform_name: str | None = None,
) -> DeploymentGuardEvidence:
    """常态仍保留跨重启 drop-in，但计划门禁文件必须不存在。"""
    _require_linux(platform_name)
    active = default_ports(plan) if ports is None else ports.validate()
    try:
        if any(active.verify_drop_in(unit) is not True for unit in GUARDED_SYSTEMD_UNITS):
            raise RuntimeDeploymentError("部署停写 drop-in 无法证明")
        if active.read_guard() is not None:
            raise RuntimeDeploymentError("部署停写门禁仍处于激活状态")
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RuntimeDeploymentError:
        raise
    except Exception:
        raise RuntimeDeploymentError("部署常态门禁无法证明") from None
    return _evidence(deployment_guard_payload(plan))


def verify_deployment_guard_marker_absent(
    plan: DeploymentPlan,
    *,
    ports: DeploymentGuardPorts | None = None,
    platform_name: str | None = None,
) -> DeploymentGuardEvidence:
    """维护窗口前只证明 marker 不存在，不要求首次部署已安装 drop-in。"""
    _require_linux(platform_name)
    active = default_ports(plan) if ports is None else ports.validate()
    try:
        if active.read_guard() is not None:
            raise RuntimeDeploymentError("部署维护前仍存在停写 marker")
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RuntimeDeploymentError:
        raise
    except Exception:
        raise RuntimeDeploymentError("部署维护前 marker 无法证明") from None
    return _evidence(deployment_guard_payload(plan))


def default_ports(plan: DeploymentPlan) -> DeploymentGuardPorts:
    if type(plan) is not DeploymentPlan:
        raise RuntimeDeploymentError("部署停写门禁计划无效")
    return DeploymentGuardPorts(
        install_drop_in=_install_drop_in,
        verify_drop_in=_verify_drop_in,
        reload_systemd=_reload_systemd,
        write_guard=_write_guard,
        read_guard=_read_guard,
        delete_guard=_delete_guard,
    )


def _install_drop_in(unit: str) -> None:
    path = _deployment_drop_in_path(unit)
    try:
        write_root_owned_regular_file_atomic(
            path,
            DEPLOYMENT_GUARD_DROP_IN_CONTENT,
            mode=0o644,
            uid=0,
            gid=0,
        )
    except TrustedManagedPathError:
        raise RuntimeDeploymentError("部署停写 drop-in 安装失败") from None


def _verify_drop_in(unit: str) -> bool:
    required = SystemdConditionSpec(
        _deployment_drop_in_path(unit),
        DEPLOYMENT_GUARD_DROP_IN_CONTENT,
    )
    allowed: tuple[SystemdConditionSpec, ...] = ()
    if unit == "codev-mcp-codegraph.service":
        allowed = (
            SystemdConditionSpec(
                CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH,
                CODEGRAPH_MAINTENANCE_GUARD_DROPIN_CONTENT,
            ),
        )
    elif unit == "codev-webhook.service":
        allowed = (
            SystemdConditionSpec(
                Path(ingress_gate_drop_in_path().as_posix()),
                INGRESS_GATE_DROP_IN_CONTENT,
            ),
        )
    try:
        verify_systemd_condition_guard(unit, required=required, allowed=allowed)
    except (SystemdConditionGuardError, RuntimeDeploymentError, ValueError):
        return False
    return True


def _deployment_drop_in_path(unit: str) -> Path:
    if unit not in GUARDED_SYSTEMD_UNITS:
        raise RuntimeDeploymentError("部署停写 unit 不在固定注册表")
    return Path(deployment_guard_drop_in_path(unit).as_posix())


def _reload_systemd() -> None:
    result = subprocess.run(
        ("/usr/bin/systemctl", "daemon-reload"),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=30.0,
    )
    if result.returncode != 0:
        raise RuntimeDeploymentError("部署停写 systemd reload 失败")


def _write_guard(payload: bytes) -> None:
    try:
        write_root_owned_regular_file_atomic(
            Path(DEPLOYMENT_GUARD_MARKER_PATH.as_posix()),
            payload,
            mode=0o600,
            uid=0,
            gid=0,
        )
    except TrustedManagedPathError:
        raise RuntimeDeploymentError("部署停写门禁写入失败") from None


def _read_guard() -> bytes | None:
    try:
        snapshot = read_optional_root_owned_regular_file_snapshot(
            Path(DEPLOYMENT_GUARD_MARKER_PATH.as_posix()),
            max_bytes=_MAX_GUARD_BYTES,
        )
    except TrustedManagedPathError:
        raise RuntimeDeploymentError("部署停写门禁不可读取") from None
    if snapshot is None:
        return None
    if (
        type(snapshot) is not RootOwnedRegularFileSnapshot
        or snapshot.mode != 0o600
        or snapshot.uid != 0
        or snapshot.gid != 0
    ):
        raise RuntimeDeploymentError("部署停写门禁元数据不受信任")
    return snapshot.content


def _delete_guard() -> None:
    if _read_guard() is None:
        raise RuntimeDeploymentError("部署停写门禁不存在")
    try:
        removed = remove_root_owned_regular_file(Path(DEPLOYMENT_GUARD_MARKER_PATH.as_posix()))
    except TrustedManagedPathError:
        raise RuntimeDeploymentError("部署停写门禁删除失败") from None
    if removed is not True:
        raise RuntimeDeploymentError("部署停写门禁不存在")


def _require_linux(platform_name: str | None) -> None:
    selected = sys.platform if platform_name is None else platform_name
    if type(selected) is not str or not selected.startswith("linux"):
        raise RuntimeDeploymentError("部署停写门禁只允许在 Linux 执行")


def _evidence(payload: bytes) -> DeploymentGuardEvidence:
    digest = hashlib.sha256(
        hashlib.sha256(DEPLOYMENT_GUARD_DROP_IN_CONTENT).hexdigest().encode("ascii")
        + b"\n"
        + payload
    ).hexdigest()
    return DeploymentGuardEvidence(digest)


__all__ = [
    "DEPLOYMENT_GUARD_DROP_IN_CONTENT",
    "GUARDED_SYSTEMD_UNITS",
    "DeploymentGuardEvidence",
    "DeploymentGuardPorts",
    "activate_deployment_guard",
    "deactivate_deployment_guard",
    "deployment_guard_payload",
    "verify_deployment_guard_active",
    "verify_deployment_guard_inactive",
    "verify_deployment_guard_marker_absent",
]
