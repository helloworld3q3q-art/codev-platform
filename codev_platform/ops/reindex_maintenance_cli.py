"""reindex 维护窗口的命令行适配层。"""
from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path


Output = Callable[[str], None]
Runner = Callable[[], None]
CodegraphRunner = Callable[[str, str], None]
LayoutMigrationRunner = Callable[[Path], None]


def cmd_reindex_maintenance(
    args: object,
    *,
    out: Output = print,
    err: Output | None = None,
    prepare_runner: Runner | None = None,
    restore_runner: Runner | None = None,
    provision_runner: Runner | None = None,
    status_proof: Runner | None = None,
    status_runner: Runner | None = None,
    resume_runner: CodegraphRunner | None = None,
    configure_resume_runner: CodegraphRunner | None = None,
    layout_migration_runner: LayoutMigrationRunner | None = None,
) -> int:
    """提供显式确认的维护与 CodeGraph 恢复命令。"""
    maintenance = _maintenance_module()
    write_error = _default_error_output if err is None else err
    action = getattr(args, "action", None)
    if action == "migrate-systemd-layout":
        manifest = _validate_layout_migration_arguments(args)
        if manifest is None:
            write_error("FATAL: systemd 布局迁移要求绝对 manifest 路径")
            return 1
        if getattr(args, "yes", False) is not True:
            out("演练：migrate-systemd-layout 会迁移 CodeGraph 主 unit；确认后请添加 --yes")
            return 0
        runner = (
            _default_layout_migration_runner
            if layout_migration_runner is None
            else layout_migration_runner
        )
        try:
            runner(manifest)
        except MemoryError:
            raise
        except Exception as error:
            _write_layout_migration_error(write_error, error)
            return 1
        out("CodeGraph systemd 主 unit 布局迁移完成")
        return 0
    if action in {"resume-codegraph", "configure-resume-codegraph"}:
        resume_arguments = _validate_codegraph_resume_arguments(args)
        if resume_arguments is None:
            write_error("FATAL: CodeGraph 恢复要求完整项目标识与目标提交")
            return 1
        project_id, target_commit = resume_arguments
        if getattr(args, "yes", False) is not True:
            out(_codegraph_dry_run_message(action))
            return 0
        runner = _select_codegraph_runner(
            action,
            resume_runner=resume_runner,
            configure_resume_runner=configure_resume_runner,
        )
        try:
            runner(project_id, target_commit)
        except MemoryError:
            raise
        except Exception as error:
            write_error(_codegraph_action_failure_message(action, error))
            return 1
        out(
            "CodeGraph 受控恢复完成"
            if action == "resume-codegraph"
            else "CodeGraph 受管恢复配置完成"
        )
        return 0
    if action == "status":
        if getattr(args, "yes", False):
            write_error("FATAL: status 不接受 --yes")
            return 1
        prove = status_runner
        if prove is None:
            prove = _default_status_runner if status_proof is None else status_proof
        try:
            prove()
        except MemoryError:
            raise
        except maintenance.ReindexMaintenanceError as error:
            write_error(f"FATAL: {error}")
            return 1
        except Exception:
            write_error("FATAL: reindex 维护窗口未就绪；安全状态未证明")
            return 1
        out("reindex 维护窗口已就绪：门禁、外部 worker、服务与 cgroup 均已证明安全")
        return 0
    if action not in {"prepare", "restore", "provision"}:
        write_error("FATAL: 不支持的 reindex 维护动作")
        return 1
    if getattr(args, "yes", False) is not True:
        out(_maintenance_dry_run_message(action))
        return 0
    runner = _select_maintenance_runner(
        action,
        prepare_runner=prepare_runner,
        restore_runner=restore_runner,
        provision_runner=provision_runner,
    )
    try:
        runner()
    except MemoryError:
        raise
    except maintenance.ReindexMaintenanceError as error:
        write_error(f"FATAL: {error}")
        return 1
    except Exception:
        write_error(f"FATAL: reindex 维护{_maintenance_action_label(action)}失败；安全状态未证明")
        return 1
    out(f"reindex 维护{action}完成")
    return 0


def register(subparsers: object) -> None:
    """注册独立维护窗口命令，避免业务队列命令承担 root 级 systemd 写入。"""
    parser = subparsers.add_parser(
        "reindex-maintenance",
        help="管理 reindex 维护窗口与受控 CodeGraph 恢复",
    )
    parser.add_argument(
        "action",
        help=(
            "provision/prepare/status/restore/migrate-systemd-layout/"
            "configure-resume-codegraph/resume-codegraph"
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="除 status 外：真正执行 root 级受控运维操作",
    )
    parser.add_argument("--project", default=None, help="CodeGraph 恢复：明确 project_id")
    parser.add_argument("--target-commit", default=None, help="CodeGraph 恢复：40/64 位完整目标提交")
    parser.add_argument("--manifest", type=Path, default=None, help="布局迁移：当前 release 的绝对安装 manifest")
    parser.set_defaults(func=cmd_reindex_maintenance)


def _codegraph_dry_run_message(action: str) -> str:
    return (
        "演练：resume-codegraph 会受控恢复 CodeGraph；确认后请添加 --yes"
        if action == "resume-codegraph"
        else "演练：configure-resume-codegraph 会写入受管配置；确认后请添加 --yes"
    )


def _validate_layout_migration_arguments(args: object) -> Path | None:
    """布局迁移只接受一个显式、绝对的 manifest，禁止混入恢复参数。"""
    manifest = getattr(args, "manifest", None)
    if (
        not isinstance(manifest, Path)
        or not (manifest.is_absolute() or manifest.as_posix().startswith("/"))
        or getattr(args, "project", None) is not None
        or getattr(args, "target_commit", None) is not None
    ):
        return None
    return manifest


def _default_layout_migration_runner(manifest: Path) -> None:
    """延迟导入窄布局事务，避免日常 CLI 注册携带 root 文件操作依赖。"""
    from codev_platform.mcp_systemd_unit_layout_migration import migrate_from_manifest

    migrate_from_manifest(manifest)


def _write_layout_migration_error(write_error: Output, error: Exception) -> None:
    from codev_platform.mcp_systemd_unit_layout_migration import (
        SystemdUnitLayoutMigrationError,
    )

    if isinstance(error, SystemdUnitLayoutMigrationError):
        write_error(f"FATAL: {error}")
        return
    write_error("FATAL: systemd 主 unit 布局迁移失败；安全状态未证明")


def _select_codegraph_runner(
    action: str,
    *,
    resume_runner: CodegraphRunner | None,
    configure_resume_runner: CodegraphRunner | None,
) -> CodegraphRunner:
    if action == "resume-codegraph":
        return _default_resume_codegraph_runner if resume_runner is None else resume_runner
    return (
        _default_configure_resume_runner
        if configure_resume_runner is None
        else configure_resume_runner
    )


def _maintenance_dry_run_message(action: str) -> str:
    if action == "provision":
        return "演练：provision 会预置 reindex 维护门禁锁；确认后请添加 --yes"
    verb = "暂停" if action == "prepare" else "恢复"
    return f"演练：{action} 会{verb} codev-reindex；确认后请添加 --yes"


def _maintenance_action_label(action: str) -> str:
    return {
        "prepare": "准备",
        "restore": "恢复",
        "provision": "预置",
    }[action]


def _select_maintenance_runner(
    action: str,
    *,
    prepare_runner: Runner | None,
    restore_runner: Runner | None,
    provision_runner: Runner | None,
) -> Runner:
    if action == "prepare":
        return _default_prepare_runner if prepare_runner is None else prepare_runner
    if action == "restore":
        return _default_restore_runner if restore_runner is None else restore_runner
    if provision_runner is not None:
        return provision_runner
    return _maintenance_module().provision_reindex_maintenance


def _codegraph_action_failure_message(action: str, error: Exception) -> str:
    """只输出受控领域错误，避免把内部异常或敏感上下文写入终端。"""
    if action == "resume-codegraph":
        from codev_platform.ops.reindex_codegraph_resume import CodegraphResumeError

        if isinstance(error, CodegraphResumeError):
            return f"FATAL: {error}"
        return "FATAL: CodeGraph 受控恢复失败；安全状态未证明"
    from codev_platform.ops.reindex_codegraph_resume_configuration import (
        CodegraphResumeConfigurationError,
    )
    from codev_platform.ops.reindex_codegraph_resume_unit_config import (
        CodegraphResumeUnitConfigurationError,
    )

    if isinstance(
        error,
        (CodegraphResumeConfigurationError, CodegraphResumeUnitConfigurationError),
    ):
        return f"FATAL: {error}"
    return "FATAL: CodeGraph 受管恢复配置失败"


def _validate_codegraph_resume_arguments(args: object) -> tuple[str, str] | None:
    """仅接受显式 project 与完整目标 OID，绝不回退 cwd 或 HEAD。"""
    try:
        from codev_platform.core.project_id import validate as validate_project_id
        from codev_platform.reindex.target_commit import require_target_commit

        return (
            validate_project_id(getattr(args, "project", None)),
            require_target_commit(getattr(args, "target_commit", None)),
        )
    except Exception:
        return None


def _default_resume_codegraph_runner(project_id: str, target_commit: str) -> None:
    """延迟导入恢复状态机，避免其异常影响既有维护命令注册。"""
    from codev_platform.ops.reindex_codegraph_resume import resume_codegraph

    resume_codegraph(project_id=project_id, target_commit=target_commit)


def _default_configure_resume_runner(project_id: str, target_commit: str) -> None:
    """在已证明的维护窗口内安装两个 unit 的同源恢复配置。"""
    from codev_platform.ops.reindex_codegraph_resume_configuration import (
        configure_codegraph_resume,
    )

    configure_codegraph_resume(project_id=project_id, target_commit=target_commit)


def _default_status_runner() -> None:
    _maintenance_module().inspect_reindex_maintenance()


def _default_prepare_runner() -> None:
    _maintenance_module().prepare_reindex_maintenance()


def _default_restore_runner() -> None:
    _maintenance_module().restore_reindex_maintenance()


def _maintenance_module():
    """延迟取得维护 facade，避免 CLI 注册阶段形成导入环。"""
    from codev_platform.ops import reindex_maintenance

    return reindex_maintenance


def _default_error_output(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


__all__ = ["cmd_reindex_maintenance", "register"]
