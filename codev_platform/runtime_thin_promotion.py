"""日常应用薄发布的原子编排契约。

本模块只负责 release 指针、受控服务刷新与补偿回滚的顺序；Git、候选
wheel、systemd manifest 和 HTTP/MCP 探针均通过窄端口注入，避免把发布
策略与具体运行环境耦合在一起。
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

from codev_platform.core.runtime_models import ActivationResult, require_sha256
from codev_platform.mcp_systemd_unit_registry import WEBHOOK_SYSTEMD_UNIT_NAME
from codev_platform.runtime_deployment_contract import RuntimeDeploymentError
from codev_platform.runtime_systemd_acceptance import STABLE_RUNTIME_UNITS


DAILY_RUNTIME_SERVICE_UNITS = (*STABLE_RUNTIME_UNITS, WEBHOOK_SYSTEMD_UNIT_NAME)


class ThinPromotionError(RuntimeDeploymentError):
    """日常薄发布无法完成，公开错误不包含路径、命令或运行时环境正文。"""


@dataclass(frozen=True, slots=True)
class ThinPromotionResult:
    """成功发布的最小脱敏回执。"""

    active_release: str
    rollback_release: str
    systemd_evidence_sha256: str
    mcp_evidence_sha256: str
    ingress_evidence_sha256: str

    def __post_init__(self) -> None:
        for field in (
            "active_release",
            "rollback_release",
            "systemd_evidence_sha256",
            "mcp_evidence_sha256",
            "ingress_evidence_sha256",
        ):
            try:
                require_sha256(getattr(self, field), field=field)
            except ValueError:
                raise ThinPromotionError("日常薄发布回执身份无效") from None
        if self.active_release == self.rollback_release:
            raise ThinPromotionError("日常薄发布目标与回滚锚点不能相同")


@dataclass(frozen=True, slots=True)
class ThinPromotionPorts:
    """编排层唯一依赖的外部操作端口。"""

    promotion_lock: Callable[[], AbstractContextManager[None]]
    preflight: Callable[[], None]
    publish_target_service_access: Callable[[str], None]
    activate: Callable[[Path, str, str], ActivationResult]
    rollback: Callable[[Path, str, str], ActivationResult]
    install_current_units: Callable[[], None]
    restart_runtime_services: Callable[[], None]
    verify_runtime_services: Callable[[], str]
    verify_mcp_services: Callable[[], str]
    verify_ingress: Callable[[], str]

    def validate(self) -> ThinPromotionPorts:
        if type(self) is not ThinPromotionPorts or not all(
            callable(value)
            for value in (
                self.preflight,
                self.promotion_lock,
                self.publish_target_service_access,
                self.activate,
                self.rollback,
                self.install_current_units,
                self.restart_runtime_services,
                self.verify_runtime_services,
                self.verify_mcp_services,
                self.verify_ingress,
            )
        ):
            raise ThinPromotionError("日常薄发布端口不可用")
        return self


def promote_staged_release(
    runtime_root: Path,
    *,
    target_release_id: str,
    rollback_release_id: str,
    ports: ThinPromotionPorts,
) -> ThinPromotionResult:
    """把已静态复验的薄 release 前移为 current，并在任一失败后受控回滚。"""
    root = _runtime_root(runtime_root)
    target = _release_id(target_release_id, "target_release")
    rollback = _release_id(rollback_release_id, "rollback_release")
    if target == rollback:
        raise ThinPromotionError("日常薄发布目标与回滚锚点不能相同")
    active = ports.validate()
    try:
        with active.promotion_lock():
            return _promote_while_locked(root, target=target, rollback=rollback, ports=active)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except ThinPromotionError:
        raise
    except Exception:
        raise ThinPromotionError("日常薄发布独占锁不可用") from None


def _promote_while_locked(
    root: Path,
    *,
    target: str,
    rollback: str,
    ports: ThinPromotionPorts,
) -> ThinPromotionResult:
    """在同一运行时部署锁内完成切换、验收与必要的补偿。"""
    preflight_failed = False
    try:
        ports.preflight()
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        preflight_failed = True
    if preflight_failed:
        raise ThinPromotionError("日常薄发布前置校验失败")

    target_access_failed = False
    try:
        ports.publish_target_service_access(target)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        target_access_failed = True
    if target_access_failed:
        raise ThinPromotionError("日常薄发布目标服务访问发布失败")

    activated = False
    promotion_failed = False
    systemd_evidence = ""
    mcp_evidence = ""
    ingress_evidence = ""
    try:
        activation = ports.activate(root, target, rollback)
        _require_activation(activation, target=target, rollback=rollback)
        activated = True
        ports.install_current_units()
        ports.restart_runtime_services()
        systemd_evidence = _evidence(ports.verify_runtime_services(), "systemd")
        mcp_evidence = _evidence(ports.verify_mcp_services(), "MCP")
        ingress_evidence = _evidence(ports.verify_ingress(), "入口")
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        promotion_failed = True
    if promotion_failed:
        if not activated:
            raise ThinPromotionError("日常薄发布切换失败")
        _restore_baseline(root, target=target, rollback=rollback, ports=ports)
        raise ThinPromotionError("日常薄发布失败，已回滚到原版本")
    return ThinPromotionResult(
        active_release=target,
        rollback_release=rollback,
        systemd_evidence_sha256=systemd_evidence,
        mcp_evidence_sha256=mcp_evidence,
        ingress_evidence_sha256=ingress_evidence,
    )


def _restore_baseline(
    runtime_root: Path,
    *,
    target: str,
    rollback: str,
    ports: ThinPromotionPorts,
) -> None:
    """恢复旧 release 对应 unit 与进程，并在任一证据缺失时失败关闭。"""
    restore_failed = False
    try:
        result = ports.rollback(runtime_root, target, rollback)
        _require_rollback(result, rollback=rollback)
        ports.install_current_units()
        ports.restart_runtime_services()
        _evidence(ports.verify_runtime_services(), "systemd")
        _evidence(ports.verify_mcp_services(), "MCP")
        _evidence(ports.verify_ingress(), "入口")
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        restore_failed = True
    if restore_failed:
        raise ThinPromotionError("日常薄发布失败且回滚状态无法证明")


def _runtime_root(value: object) -> Path:
    if not isinstance(value, Path):
        raise ThinPromotionError("日常薄发布运行时根无效")
    return value


def _release_id(value: object, field: str) -> str:
    try:
        return require_sha256(value, field=field)
    except ValueError:
        raise ThinPromotionError("日常薄发布 release ID 无效") from None


def _evidence(value: object, label: str) -> str:
    try:
        return require_sha256(value, field=f"{label}_evidence")
    except ValueError:
        raise ThinPromotionError("日常薄发布验收证据无效") from None


def _require_activation(
    result: object,
    *,
    target: str,
    rollback: str,
) -> None:
    if type(result) is not ActivationResult or (
        result.active_release != target or result.previous_release != rollback
    ):
        raise ThinPromotionError("日常薄发布激活回执不一致")


def _require_rollback(result: object, *, rollback: str) -> None:
    if type(result) is not ActivationResult or (
        result.active_release != rollback or result.previous_release != rollback
    ):
        raise ThinPromotionError("日常薄发布回滚回执不一致")


__all__ = [
    "DAILY_RUNTIME_SERVICE_UNITS",
    "ThinPromotionError",
    "ThinPromotionPorts",
    "ThinPromotionResult",
    "promote_staged_release",
]
