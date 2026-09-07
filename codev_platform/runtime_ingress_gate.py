"""Webhook 最终验收前跨重启保持关闭的持久门禁。"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from codev_platform.ops.systemd_condition_guard import (
    SystemdConditionGuardError,
    SystemdConditionSpec,
    verify_systemd_condition_guard,
)
from codev_platform.runtime_deployment_contract import DeploymentPlan, RuntimeDeploymentError
from codev_platform.runtime_systemd_gate_contract import (
    DEPLOYMENT_GUARD_DROP_IN_CONTENT,
    INGRESS_GATE_DROP_IN_CONTENT,
    INGRESS_GATE_MARKER_PATH,
    deployment_guard_drop_in_path,
    ingress_gate_drop_in_path,
)
_MAX_GATE_BYTES = 4096
_WEBHOOK_UNIT = "codev-webhook.service"


@dataclass(frozen=True, slots=True)
class IngressGateEvidence:
    """入口门禁固定载荷与本次部署计划的合并证据。"""

    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class IngressGatePorts:
    """入口领域只依赖固定 drop-in 和单个门禁文件的窄端口。"""

    install_drop_in: Callable[[], None]
    verify_drop_in: Callable[[], bool]
    reload_systemd: Callable[[], None]
    write_guard: Callable[[bytes], None]
    read_guard: Callable[[], bytes | None]
    delete_guard: Callable[[], None]

    def validate(self) -> IngressGatePorts:
        if type(self) is not IngressGatePorts or not all(
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
            raise RuntimeDeploymentError("入口门禁端口不可用")
        return self


def ingress_gate_payload(plan: DeploymentPlan) -> bytes:
    """只持久化计划摘要与目标提交，不记录配置正文或秘密。"""
    if type(plan) is not DeploymentPlan:
        raise RuntimeDeploymentError("入口门禁计划无效")
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


def activate_ingress_gate(
    plan: DeploymentPlan,
    *,
    ports: IngressGatePorts | None = None,
    platform_name: str | None = None,
) -> IngressGateEvidence:
    """安装固定 systemd 条件并创建与部署计划绑定的入口标记。"""
    _require_linux(platform_name)
    active = default_ports() if ports is None else ports.validate()
    try:
        active.install_drop_in()
        active.reload_systemd()
        if active.verify_drop_in() is not True:
            raise RuntimeDeploymentError("入口门禁 drop-in 无法证明")
        payload = ingress_gate_payload(plan)
        active.write_guard(payload)
        if active.read_guard() != payload:
            raise RuntimeDeploymentError("入口门禁身份不一致")
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RuntimeDeploymentError:
        raise
    except Exception:
        raise RuntimeDeploymentError("入口门禁激活失败") from None
    return _evidence(payload)


def verify_ingress_gate_active(
    plan: DeploymentPlan,
    *,
    ports: IngressGatePorts | None = None,
    platform_name: str | None = None,
) -> IngressGateEvidence:
    """续跑前同时复验固定 drop-in 与本次计划的门禁内容。"""
    _require_linux(platform_name)
    active = default_ports() if ports is None else ports.validate()
    try:
        if active.verify_drop_in() is not True:
            raise RuntimeDeploymentError("入口门禁 drop-in 无法证明")
        payload = ingress_gate_payload(plan)
        if active.read_guard() != payload:
            raise RuntimeDeploymentError("入口门禁身份不一致")
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RuntimeDeploymentError:
        raise
    except Exception:
        raise RuntimeDeploymentError("入口门禁无法证明") from None
    return _evidence(payload)


def deactivate_ingress_gate(
    plan: DeploymentPlan,
    *,
    ports: IngressGatePorts | None = None,
    platform_name: str | None = None,
) -> IngressGateEvidence:
    """仅在调用方完成最终验收后删除入口标记，固定 drop-in 永久保留。"""
    _require_linux(platform_name)
    active = default_ports() if ports is None else ports.validate()
    evidence = verify_ingress_gate_active(plan, ports=active, platform_name=platform_name)
    try:
        active.delete_guard()
        if active.read_guard() is not None:
            raise RuntimeDeploymentError("入口门禁删除后仍存在")
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RuntimeDeploymentError:
        raise
    except Exception:
        raise RuntimeDeploymentError("入口门禁无法安全删除") from None
    return evidence


def verify_ingress_gate_inactive(
    plan: DeploymentPlan,
    *,
    ports: IngressGatePorts | None = None,
    platform_name: str | None = None,
) -> IngressGateEvidence:
    """常态要求固定 drop-in 仍受信，而入口标记已经不存在。"""
    _require_linux(platform_name)
    active = default_ports() if ports is None else ports.validate()
    try:
        if active.verify_drop_in() is not True:
            raise RuntimeDeploymentError("入口门禁 drop-in 无法证明")
        if active.read_guard() is not None:
            raise RuntimeDeploymentError("入口门禁仍处于关闭状态")
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RuntimeDeploymentError:
        raise
    except Exception:
        raise RuntimeDeploymentError("入口常态门禁无法证明") from None
    return _evidence(ingress_gate_payload(plan))


def verify_ingress_gate_marker_absent(
    plan: DeploymentPlan,
    *,
    ports: IngressGatePorts | None = None,
    platform_name: str | None = None,
) -> IngressGateEvidence:
    """切换前只证明入口 marker 不存在，不要求首次部署已安装 drop-in。"""
    _require_linux(platform_name)
    active = default_ports() if ports is None else ports.validate()
    try:
        if active.read_guard() is not None:
            raise RuntimeDeploymentError("部署切换前仍存在入口 marker")
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RuntimeDeploymentError:
        raise
    except Exception:
        raise RuntimeDeploymentError("部署切换前入口 marker 无法证明") from None
    return _evidence(ingress_gate_payload(plan))


def default_ports() -> IngressGatePorts:
    """生产端口复用现有 root 可信 dirfd 文件原语，避免路径重开竞态。"""
    return IngressGatePorts(
        install_drop_in=_install_drop_in,
        verify_drop_in=_verify_drop_in,
        reload_systemd=_reload_systemd,
        write_guard=_write_guard,
        read_guard=_read_guard,
        delete_guard=_delete_guard,
    )


def _install_drop_in() -> None:
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        TrustedManagedPathError,
        write_root_owned_regular_file_atomic,
    )

    try:
        write_root_owned_regular_file_atomic(
            Path(ingress_gate_drop_in_path().as_posix()),
            INGRESS_GATE_DROP_IN_CONTENT,
            mode=0o644,
        )
    except TrustedManagedPathError:
        raise RuntimeDeploymentError("入口门禁 drop-in 安装失败") from None


def _verify_drop_in() -> bool:
    try:
        verify_systemd_condition_guard(
            _WEBHOOK_UNIT,
            required=SystemdConditionSpec(
                Path(ingress_gate_drop_in_path().as_posix()),
                INGRESS_GATE_DROP_IN_CONTENT,
            ),
            allowed=(
                SystemdConditionSpec(
                    Path(deployment_guard_drop_in_path(_WEBHOOK_UNIT).as_posix()),
                    DEPLOYMENT_GUARD_DROP_IN_CONTENT,
                ),
            ),
        )
    except (SystemdConditionGuardError, ValueError):
        return False
    return True


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
        raise RuntimeDeploymentError("入口门禁 systemd reload 失败")


def _write_guard(payload: bytes) -> None:
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        TrustedManagedPathError,
        write_root_owned_regular_file_atomic,
    )

    try:
        write_root_owned_regular_file_atomic(
            Path(INGRESS_GATE_MARKER_PATH.as_posix()),
            payload,
            mode=0o600,
        )
    except TrustedManagedPathError:
        raise RuntimeDeploymentError("入口门禁标记写入失败") from None


def _read_guard() -> bytes | None:
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        TrustedManagedPathError,
        read_optional_root_owned_regular_file_snapshot,
    )

    try:
        snapshot = read_optional_root_owned_regular_file_snapshot(
            Path(INGRESS_GATE_MARKER_PATH.as_posix()),
            max_bytes=_MAX_GATE_BYTES,
        )
    except TrustedManagedPathError:
        raise RuntimeDeploymentError("入口门禁标记不可读取") from None
    if snapshot is None:
        return None
    if snapshot.mode != 0o600 or snapshot.uid != 0 or snapshot.gid != 0:
        raise RuntimeDeploymentError("入口门禁标记元数据不受信任")
    return snapshot.content


def _delete_guard() -> None:
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        TrustedManagedPathError,
        remove_root_owned_regular_file,
    )

    try:
        removed = remove_root_owned_regular_file(Path(INGRESS_GATE_MARKER_PATH.as_posix()))
    except TrustedManagedPathError:
        raise RuntimeDeploymentError("入口门禁标记删除失败") from None
    if removed is not True:
        raise RuntimeDeploymentError("入口门禁标记不存在")


def _require_linux(platform_name: str | None) -> None:
    selected = sys.platform if platform_name is None else platform_name
    if type(selected) is not str or not selected.startswith("linux"):
        raise RuntimeDeploymentError("入口门禁只允许在 Linux 执行")


def _evidence(payload: bytes) -> IngressGateEvidence:
    digest = hashlib.sha256(
        hashlib.sha256(INGRESS_GATE_DROP_IN_CONTENT).hexdigest().encode("ascii")
        + b"\n"
        + payload
    ).hexdigest()
    return IngressGateEvidence(digest)


__all__ = [
    "INGRESS_GATE_DROP_IN_CONTENT",
    "IngressGateEvidence",
    "IngressGatePorts",
    "activate_ingress_gate",
    "deactivate_ingress_gate",
    "ingress_gate_payload",
    "verify_ingress_gate_active",
    "verify_ingress_gate_inactive",
    "verify_ingress_gate_marker_absent",
]
