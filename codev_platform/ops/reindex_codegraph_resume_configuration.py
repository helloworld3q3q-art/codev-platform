"""CodeGraph 受管恢复配置的维护窗口编排。"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import NoReturn

from codev_platform.ops.reindex_codegraph_resume_contract import (
    codegraph_resume_environment_files_reason_label,
    codegraph_resume_failure_stage_label,
    codegraph_resume_proof_dropin_active_label,
    codegraph_resume_proof_location_label,
)
from codev_platform.ops.reindex_codegraph_resume_context import CodegraphResumeContext


class CodegraphResumeConfigurationError(RuntimeError):
    """CodeGraph 受管恢复配置无法在已证明的维护态完成。"""


ContextResolver = Callable[[str, str], CodegraphResumeContext]
MaintenancePermit = Callable[[], AbstractContextManager[bool]]
MaintenanceAction = Callable[[], None]
UnitInstaller = Callable[..., None]


@dataclass(frozen=True, slots=True)
class CodegraphResumeConfigurationPorts:
    """配置编排的窄端口，隔离维护门禁、上下文和文件安装细节。"""

    resolve_context: ContextResolver
    maintenance_permit: MaintenancePermit
    inspect_maintenance: MaintenanceAction
    prepare_maintenance: MaintenanceAction
    install_units: UnitInstaller


def configure_codegraph_resume(
    *,
    project_id: str,
    target_commit: str,
    ports: CodegraphResumeConfigurationPorts | None = None,
) -> None:
    """仅在维护许可内安装同源恢复配置，并在失败后重新收敛维护态。"""
    active_ports = _default_ports() if ports is None else ports
    _require_ports(active_ports)
    context = _resolve_context(active_ports, project_id, target_commit)
    _install_in_maintenance_window(active_ports, context)


def _resolve_context(
    ports: CodegraphResumeConfigurationPorts,
    project_id: str,
    target_commit: str,
) -> CodegraphResumeContext:
    try:
        return ports.resolve_context(project_id, target_commit)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise CodegraphResumeConfigurationError("CodeGraph 恢复配置上下文无法解析") from error


def _install_in_maintenance_window(
    ports: CodegraphResumeConfigurationPorts,
    context: CodegraphResumeContext,
) -> None:
    maintenance_confirmed = False
    try:
        with ports.maintenance_permit() as permitted:
            if permitted is not True:
                raise CodegraphResumeConfigurationError("CodeGraph 恢复配置维护窗口未证明")
            ports.inspect_maintenance()
            maintenance_confirmed = True
            ports.install_units(
                config_path=context.config_path,
                data_root=context.data_root,
                config_digest=context.config_digest,
                service_environment_path=context.service_environment_path,
            )
            ports.inspect_maintenance()
    except BaseException as error:
        if not maintenance_confirmed:
            _raise_without_settlement(error)
        _raise_after_settlement(error, _return_to_maintenance(ports))


def _return_to_maintenance(ports: CodegraphResumeConfigurationPorts) -> bool:
    try:
        ports.prepare_maintenance()
        ports.inspect_maintenance()
        return True
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        return False


def _raise_without_settlement(error: BaseException) -> NoReturn:
    if isinstance(error, (KeyboardInterrupt, SystemExit, MemoryError)):
        raise error
    if isinstance(error, CodegraphResumeConfigurationError):
        raise error
    raise CodegraphResumeConfigurationError("CodeGraph 恢复配置维护状态未证明") from error


def _raise_after_settlement(error: BaseException, recovered: bool) -> NoReturn:
    if isinstance(error, (KeyboardInterrupt, SystemExit, MemoryError)):
        raise error
    message = (
        "CodeGraph 受管恢复配置失败；已回到维护状态"
        if recovered
        else "CodeGraph 受管恢复配置失败；安全状态未证明"
    )
    stage_label = codegraph_resume_failure_stage_label(getattr(error, "stage", None))
    if stage_label is not None:
        message = f"{message}；阶段={stage_label}"
    proof_label = codegraph_resume_proof_location_label(
        getattr(error, "proof_unit", None),
        getattr(error, "proof_property", None),
    )
    if stage_label == "同源证明" and proof_label is not None:
        message = f"{message}；证明={proof_label}"
    dropin_label = codegraph_resume_proof_dropin_active_label(
        getattr(error, "proof_dropin_active", None)
    )
    if stage_label == "同源证明" and proof_label is not None and dropin_label is not None:
        message = f"{message}；{dropin_label}"
    reason_label = codegraph_resume_environment_files_reason_label(
        getattr(error, "proof_environment_files_reason", None)
    )
    if stage_label == "同源证明" and proof_label is not None and reason_label is not None:
        message = f"{message}；原因={reason_label}"
    raise CodegraphResumeConfigurationError(message) from error


def _require_ports(ports: CodegraphResumeConfigurationPorts) -> None:
    if not isinstance(ports, CodegraphResumeConfigurationPorts) or not all(
        callable(item)
        for item in (
            ports.resolve_context,
            ports.maintenance_permit,
            ports.inspect_maintenance,
            ports.prepare_maintenance,
            ports.install_units,
        )
    ):
        raise CodegraphResumeConfigurationError("CodeGraph 恢复配置适配器不可用")


def _default_ports() -> CodegraphResumeConfigurationPorts:
    return CodegraphResumeConfigurationPorts(
        resolve_context=_default_context_resolver,
        maintenance_permit=_default_maintenance_permit,
        inspect_maintenance=_default_inspect_maintenance,
        prepare_maintenance=_default_prepare_maintenance,
        install_units=_default_unit_installer,
    )


def _default_context_resolver(project_id: str, target_commit: str) -> CodegraphResumeContext:
    from codev_platform.ops.reindex_codegraph_resume_context import (
        resolve_codegraph_resume_context,
    )

    return resolve_codegraph_resume_context(project_id, target_commit)


def _default_maintenance_permit() -> AbstractContextManager[bool]:
    from codev_platform.reindex.maintenance_gate import maintenance_admin_window_permit

    return maintenance_admin_window_permit()


def _default_inspect_maintenance() -> None:
    from codev_platform.ops.reindex_maintenance import inspect_reindex_maintenance

    inspect_reindex_maintenance()


def _default_prepare_maintenance() -> None:
    from codev_platform.ops.reindex_maintenance import prepare_reindex_maintenance

    prepare_reindex_maintenance()


def _default_unit_installer(**kwargs: object) -> None:
    from codev_platform.ops.reindex_codegraph_resume_unit_config import (
        configure_codegraph_resume_units,
    )

    configure_codegraph_resume_units(**kwargs)


__all__ = [
    "CodegraphResumeConfigurationError",
    "CodegraphResumeConfigurationPorts",
    "configure_codegraph_resume",
]
