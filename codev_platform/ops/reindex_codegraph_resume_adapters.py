"""CodeGraph 恢复状态机的生产适配器。"""

from __future__ import annotations

import os
from collections.abc import Iterable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TYPE_CHECKING

from codev_platform.ops.reindex_codegraph_resume_context import CodegraphResumeContext
from codev_platform.ops.reindex_codegraph_runtime_identity import CodegraphRuntimeIdentity
from codev_platform.ops.reindex_restore_handoff import ReindexRestoreHandoff

if TYPE_CHECKING:
    from codev_platform.ops.reindex_codegraph_startup_bridge_config import (
        CodegraphStartupBridgeSpec,
    )


def default_context_resolver(
    project_id: str,
    target_commit: str,
) -> CodegraphResumeContext:
    from codev_platform.ops.reindex_codegraph_resume_context import (
        resolve_codegraph_resume_context,
    )

    return resolve_codegraph_resume_context(project_id, target_commit)


def default_configuration_proof(context: CodegraphResumeContext) -> None:
    from codev_platform.ops.reindex_codegraph_resume_config_proof import (
        verify_codegraph_resume_configuration,
    )

    verify_codegraph_resume_configuration(
        config_path=context.config_path,
        data_root=context.data_root,
        config_digest=context.config_digest,
    )


def default_prepare_maintenance() -> None:
    from codev_platform.ops.reindex_maintenance import prepare_reindex_maintenance

    prepare_reindex_maintenance()


def default_inspect_maintenance() -> None:
    from codev_platform.ops.reindex_maintenance import inspect_reindex_maintenance

    inspect_reindex_maintenance()


def default_operation_leases(
    repositories: Iterable[Path],
) -> AbstractContextManager[None]:
    from codev_platform.codegraph.operation_lease import codegraph_operation_leases

    return codegraph_operation_leases(repositories)


def default_manifest_proof(context: CodegraphResumeContext) -> None:
    from codev_platform.ops.reindex_codegraph_manifest_proof import (
        require_codegraph_manifest_target_ok,
    )

    require_codegraph_manifest_target_ok(
        context.project_id,
        context.target_commit,
        context.runtime_revision,
        path=context.manifest_path,
    )


def default_prepare_reindex_handoff(
    context: CodegraphResumeContext,
    identity: CodegraphRuntimeIdentity,
) -> ReindexRestoreHandoff:
    """在会话锁内完成 M1→M3；最终 marker 提交由状态机的短锁阶段执行。"""
    from codev_platform.ops.reindex_restore_handoff import (
        prepare_reindex_restore_handoff,
        prove_reindex_restore_handoff_while_session_locked,
        settle_reindex_restore_handoff_while_session_locked,
    )

    _prove_codegraph_and_held_webhook(context, identity)
    handoff = prepare_reindex_restore_handoff()
    settle_reindex_restore_handoff_while_session_locked(handoff)
    _prove_codegraph_and_held_webhook(context, identity)
    default_resume_ingress(context)
    _prove_final_data_plane(context, identity)
    prove_reindex_restore_handoff_while_session_locked(handoff)
    return handoff


def default_complete_reindex_handoff_locked(
    handoff: ReindexRestoreHandoff,
) -> None:
    """仅由状态机持短 gate EX 调用，删除 marker 后不再执行外部动作。"""
    from codev_platform.ops.reindex_restore_handoff import (
        complete_reindex_restore_handoff_while_transition_locked,
    )

    complete_reindex_restore_handoff_while_transition_locked(handoff)


def default_codegraph_maintenance_proof() -> None:
    from codev_platform.ops.reindex_codegraph_lifecycle import (
        verify_codegraph_maintenance,
    )

    verify_codegraph_maintenance()


def default_staged_payload_proof(context: CodegraphResumeContext) -> None:
    from codev_platform.mcp_systemd_stage_receipt import prove_staged_systemd_payload
    from codev_platform.ops.reindex_codegraph_resume_health import (
        require_codegraph_health_port,
    )

    prove_staged_systemd_payload(
        context.runtime_release,
        require_effective=False,
        codegraph_port=require_codegraph_health_port(context.health_url),
    )


def default_clear_codegraph_startup_bridge(context: CodegraphResumeContext) -> None:
    """M0 仅清理当前 controller 精确签发的残留 bridge。"""
    from codev_platform.ops.reindex_codegraph_startup_bridge_config import (
        clear_codegraph_startup_bridge,
    )

    clear_codegraph_startup_bridge(_codegraph_startup_bridge_spec(context))


def default_install_codegraph_startup_bridge(context: CodegraphResumeContext) -> None:
    """在 M1 解 mask 前安装一次性 bridge，避免冻结服务读取旧门禁。"""
    from codev_platform.ops.reindex_codegraph_startup_bridge_config import (
        install_codegraph_startup_bridge,
    )

    install_codegraph_startup_bridge(_codegraph_startup_bridge_spec(context))


def default_remove_codegraph_startup_bridge(context: CodegraphResumeContext) -> None:
    """初始健康后删除 bridge，强制后续重启回到 canonical ExecStart。"""
    from codev_platform.ops.reindex_codegraph_startup_bridge_config import (
        remove_codegraph_startup_bridge,
    )

    remove_codegraph_startup_bridge(_codegraph_startup_bridge_spec(context))


def default_remove_runtime_mask() -> None:
    from codev_platform.ops.reindex_codegraph_maintenance import (
        remove_codegraph_runtime_mask,
    )

    remove_codegraph_runtime_mask()


def default_codegraph_effective_payload_proof(
    context: CodegraphResumeContext,
) -> None:
    _prove_codegraph_effective_payload(
        context,
        bridge=_codegraph_startup_bridge_spec(context),
    )


def default_codegraph_normal_effective_payload_proof(
    context: CodegraphResumeContext,
) -> None:
    """bridge 删除后复证 canonical 启动命令，拒绝隐藏的 controller 依赖。"""
    _prove_codegraph_effective_payload(context, bridge=None)


def _prove_codegraph_effective_payload(
    context: CodegraphResumeContext,
    *,
    bridge: CodegraphStartupBridgeSpec | None,
) -> None:
    from codev_platform.mcp_systemd_stage_receipt import prove_staged_systemd_payload
    from codev_platform.ops.reindex_maintenance import (
        prove_reindex_maintenance_dropin,
    )
    from codev_platform.ops.reindex_codegraph_resume_unit_config import (
        render_codegraph_resume_dropin_content,
    )
    from codev_platform.ops.reindex_codegraph_resume_health import (
        require_codegraph_health_port,
    )

    bridge_content = None if bridge is None else bridge.dropin_content
    bridge_exec_start = None if bridge is None else bridge.exec_start
    prove_reindex_maintenance_dropin()
    prove_staged_systemd_payload(
        context.runtime_release,
        require_effective=True,
        allow_reindex_local_dropins=True,
        codegraph_port=require_codegraph_health_port(context.health_url),
        resume_dropin_content=render_codegraph_resume_dropin_content(
            context.service_environment_path
        ),
        codegraph_startup_bridge_dropin_content=bridge_content,
        codegraph_effective_exec_start=bridge_exec_start,
    )


def _codegraph_startup_bridge_spec(
    context: CodegraphResumeContext,
) -> CodegraphStartupBridgeSpec:
    """统一从已验证恢复上下文派生 frozen interpreter、controller 脚本和端口。"""
    from codev_platform.ops.reindex_codegraph_resume_health import (
        require_codegraph_health_port,
    )
    from codev_platform.ops.reindex_codegraph_startup_bridge_config import (
        current_codegraph_startup_bridge_spec,
    )

    return current_codegraph_startup_bridge_spec(
        Path(context.runtime_release.interpreter_path),
        require_codegraph_health_port(context.health_url),
    )


def default_start_codegraph() -> None:
    from codev_platform.ops.reindex_codegraph_lifecycle import start_codegraph_service

    start_codegraph_service()


def default_codegraph_running_proof(_context: CodegraphResumeContext) -> None:
    from codev_platform.ops.reindex_admin_systemd_guard import (
        verify_codev_codegraph_running,
    )

    verify_codev_codegraph_running()


def default_codegraph_identity_reader(
    _context: CodegraphResumeContext,
) -> CodegraphRuntimeIdentity:
    from codev_platform.ops.reindex_codegraph_runtime_identity import (
        read_codegraph_runtime_identity,
    )

    return read_codegraph_runtime_identity()


def default_codegraph_health_proof(context: CodegraphResumeContext) -> None:
    from codev_platform.ops.reindex_codegraph_resume_health import (
        prove_codegraph_health,
    )

    prove_codegraph_health(context.health_url)


def default_codegraph_stability_proof(
    _context: CodegraphResumeContext,
    identity: CodegraphRuntimeIdentity,
) -> None:
    from codev_platform.ops.reindex_codegraph_runtime_identity import (
        prove_codegraph_runtime_stable,
    )

    prove_codegraph_runtime_stable(identity)


def default_enable_codegraph() -> None:
    from codev_platform.ops.reindex_codegraph_lifecycle import enable_codegraph_service

    enable_codegraph_service()


def default_remove_codegraph_hold() -> None:
    from codev_platform.ops.reindex_codegraph_maintenance_hold import (
        deactivate_codegraph_maintenance_hold,
    )

    deactivate_codegraph_maintenance_hold()


def default_resume_ingress(context: CodegraphResumeContext) -> None:
    """在 marker 仍有效时完成 Webhook 开放验收，worker 继续无索引写许可。"""
    _load_verified_webhook_config(context)
    from codev_platform.ops.reindex_webhook_maintenance import (
        resume_webhook_maintenance,
    )
    from codev_platform.runtime_webhook_acceptance import verify_webhook_http

    def prove_health() -> None:
        verify_webhook_http(_load_verified_webhook_config(context))

    resume_webhook_maintenance(health_proof=prove_health)


def _prove_codegraph_and_held_webhook(
    context: CodegraphResumeContext,
    identity: CodegraphRuntimeIdentity,
) -> None:
    _prove_codegraph_ready(context, identity)
    from codev_platform.ops.reindex_webhook_maintenance import verify_webhook_maintenance

    verify_webhook_maintenance()


def _prove_final_data_plane(
    context: CodegraphResumeContext,
    identity: CodegraphRuntimeIdentity,
) -> None:
    _prove_codegraph_ready(context, identity)
    configuration = _load_verified_webhook_config(context)
    from codev_platform.runtime_systemd_acceptance import verify_ingress_stability
    from codev_platform.runtime_webhook_acceptance import verify_webhook_http

    verify_ingress_stability()
    verify_webhook_http(configuration)


def _prove_codegraph_ready(
    context: CodegraphResumeContext,
    identity: CodegraphRuntimeIdentity,
) -> None:
    if type(identity) is not CodegraphRuntimeIdentity:
        raise RuntimeError("CodeGraph 恢复实例身份无效")
    default_codegraph_running_proof(context)
    if default_codegraph_identity_reader(context) != identity:
        raise RuntimeError("CodeGraph 恢复实例身份已变化")
    default_codegraph_health_proof(context)
    default_codegraph_stability_proof(context, identity)


def _load_verified_webhook_config(context: CodegraphResumeContext) -> dict:
    """复用恢复快照的显式 overlay 与摘要，拒绝入口开放前配置漂移。"""
    if type(context) is not CodegraphResumeContext:
        raise RuntimeError("Webhook 恢复上下文无效")
    raw = os.environ.get("CODEV_PLATFORM_CONFIG")
    try:
        if type(raw) is not str or Path(raw).resolve(strict=True) != context.config_path:
            raise ValueError("配置覆盖漂移")
        from codev_platform.core.config import config_snapshot_digest, load_config

        config = load_config()
        if config_snapshot_digest(config) != context.config_digest:
            raise ValueError("配置摘要漂移")
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise RuntimeError("Webhook 恢复配置无法证明") from None
    return config


def default_settle_maintenance_locked(context: CodegraphResumeContext) -> bool:
    """锁内补偿同时清除 bridge，并在 M0 最终复证后才报告成功。"""
    from codev_platform.ops.reindex_combined_maintenance_settlement import (
        settle_combined_maintenance_while_transition_locked,
    )

    settlement = settle_combined_maintenance_while_transition_locked()
    try:
        default_clear_codegraph_startup_bridge(context)
        default_inspect_maintenance()
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        return False
    return settlement.proven


def default_transition_lock() -> AbstractContextManager[None]:
    """与 installer、布局迁移、prepare 复用同一把全局 systemd 转换锁。"""
    from codev_platform.reindex.maintenance_gate import (
        maintenance_systemd_transition_lock,
    )

    return maintenance_systemd_transition_lock()


def default_transition_session_lock() -> AbstractContextManager[None]:
    """恢复会话锁仅串行管理员状态机，不参与 worker 的共享准入。"""
    from codev_platform.reindex.maintenance_gate import (
        maintenance_systemd_transition_session,
    )

    return maintenance_systemd_transition_session()


__all__ = [
    "default_codegraph_effective_payload_proof",
    "default_codegraph_health_proof",
    "default_codegraph_identity_reader",
    "default_codegraph_maintenance_proof",
    "default_codegraph_running_proof",
    "default_codegraph_stability_proof",
    "default_codegraph_normal_effective_payload_proof",
    "default_clear_codegraph_startup_bridge",
    "default_configuration_proof",
    "default_context_resolver",
    "default_enable_codegraph",
    "default_inspect_maintenance",
    "default_install_codegraph_startup_bridge",
    "default_manifest_proof",
    "default_operation_leases",
    "default_prepare_maintenance",
    "default_remove_codegraph_hold",
    "default_remove_codegraph_startup_bridge",
    "default_remove_runtime_mask",
    "default_complete_reindex_handoff_locked",
    "default_prepare_reindex_handoff",
    "default_resume_ingress",
    "default_settle_maintenance_locked",
    "default_staged_payload_proof",
    "default_start_codegraph",
    "default_transition_session_lock",
    "default_transition_lock",
]
