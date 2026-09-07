"""版本化 WSL 运行时 CLI：只组合底层公开能力并输出无秘密 JSON。"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codev_platform.core.runtime_models import RuntimeIdentity, require_sha256


@dataclass(frozen=True)
class _RuntimePorts:
    """CLI 与构建/发布领域层之间的窄端口，便于独立验证编排。"""

    load_config: Callable[[], dict[str, Any]]
    release_root: Callable[[dict[str, Any] | None, Path | None], Path]
    build_hashed_lock: Callable[[Path, Path, Path, Path], object]
    build_base: Callable[[Path, Path, Path, Path | None], object]
    build_candidate: Callable[[Path, Path, str | None], tuple[Path, Path, object]]
    stage_release: Callable[[Path, Path, Path, str], object]
    verify_release: Callable[[Path, str], object]
    runtime_identity: Callable[[], RuntimeIdentity]
    activate_release: Callable[[Path, str], object]
    activate_release_with_rollback_anchor: Callable[[Path, str, str], object]
    rollback_release: Callable[[Path], object]
    collision_errors: tuple[type[Exception], ...]
    operation_errors: tuple[type[Exception], ...]


class _InvalidRuntimeId(ValueError):
    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class _RuntimeStatusUnavailable(RuntimeError):
    """当前进程不属于已验证 release，不能冒充生产运行时状态。"""


class _ManagedRuntimeRootRequired(RuntimeError):
    """受管对象动作不得回退到 sudo/root 的 HOME。"""


_ACTION_KIND = {
    "lock": "requirements_lock",
    "base": "base",
    "build": "candidate",
    "stage": "release",
    "verify": "release",
    "status": "runtime",
    "activate": "release",
    "rollback": "release",
}


def _default_ports() -> _RuntimePorts:
    """延迟装配公开函数，CLI 注册阶段不触发构建或运行时探测。"""
    from codev_platform.core.config import load_config
    from codev_platform.core.runtime_identity import RuntimeIdentityError, runtime_identity
    from codev_platform.core.runtime_models import RuntimeModelError, current_abi
    from codev_platform.runtime_base import RuntimeBaseError, build_base
    from codev_platform.runtime_build import (
        RuntimeBuildError,
        build_candidate,
        stage_release,
        verify_release,
    )
    from codev_platform.runtime_errors import RuntimeIdCollisionError
    from codev_platform.runtime_lock import RuntimeLockError, build_hashed_lock
    from codev_platform.runtime_release import (
        RuntimeReleaseError,
        activate_release,
        activate_release_with_rollback_anchor,
        release_root,
        rollback_release,
    )
    from codev_platform.runtime_target import RuntimeTargetError, require_server_runtime_target

    def build_server_lock(freeze: Path, approved: Path, output: Path, wheelhouse: Path):
        require_server_runtime_target(current_abi())
        return build_hashed_lock(
            freeze,
            approved,
            output,
            wheelhouse,
            progress=_emit_lock_progress,
        )

    def build_server_base(root: Path, lock: Path, approved: Path, wheelhouse: Path):
        require_server_runtime_target(current_abi())
        return build_base(root, lock, approved, wheelhouse)

    return _RuntimePorts(
        load_config=load_config,
        release_root=release_root,
        build_hashed_lock=build_server_lock,
        build_base=build_server_base,
        build_candidate=build_candidate,
        stage_release=stage_release,
        verify_release=verify_release,
        runtime_identity=runtime_identity,
        activate_release=activate_release,
        activate_release_with_rollback_anchor=activate_release_with_rollback_anchor,
        rollback_release=rollback_release,
        collision_errors=(RuntimeIdCollisionError,),
        operation_errors=(
            RuntimeLockError,
            RuntimeBaseError,
            RuntimeBuildError,
            RuntimeReleaseError,
            RuntimeIdentityError,
            RuntimeModelError,
            RuntimeTargetError,
            OSError,
            ValueError,
            TypeError,
        ),
    )


def _runtime_root(args: argparse.Namespace, ports: _RuntimePorts) -> Path:
    explicit = getattr(args, "runtime_root", None)
    cfg = ports.load_config()
    runtime = cfg.get("runtime") if isinstance(cfg, dict) else None
    configured = runtime.get("release_root") if isinstance(runtime, dict) else None
    if explicit is None and not configured:
        raise _ManagedRuntimeRootRequired
    return ports.release_root(cfg, explicit)


def _full_id(value: object, kind: str) -> str:
    try:
        return require_sha256(value, field=f"{kind}_id")
    except ValueError:
        raise _InvalidRuntimeId(kind) from None


def _dispatch(args: argparse.Namespace, ports: _RuntimePorts) -> object:
    action = args.runtime_action
    if action == "lock":
        return ports.build_hashed_lock(
            args.raw_freeze,
            args.approved_requirements,
            args.output,
            args.wheelhouse,
        )
    if action == "base":
        return ports.build_base(
            _runtime_root(args, ports),
            args.lock,
            args.approved_requirements,
            args.wheelhouse,
        )
    if action == "build":
        arguments = (args.repo, args.out_dir)
        source_user = getattr(args, "source_user", None)
        if source_user is not None:
            if args.revision is None:
                raise ValueError("服务账号候选构建必须指定精确提交")
            _wheel, _candidate_file, candidate = ports.build_candidate(
                *arguments,
                args.revision,
                source_user=source_user,
            )
            return candidate
        if args.revision is None:
            _wheel, _candidate_file, candidate = ports.build_candidate(*arguments)
        else:
            _wheel, _candidate_file, candidate = ports.build_candidate(
                *arguments,
                args.revision,
            )
        return candidate
    if action == "stage":
        base_id = _full_id(args.base_id, "base")
        return ports.stage_release(_runtime_root(args, ports), args.wheel, args.candidate, base_id)
    if action == "verify":
        release_id = _full_id(args.release_id, "release")
        return ports.verify_release(_runtime_root(args, ports), release_id)
    if action == "status":
        identity = ports.runtime_identity()
        if identity.mode != "release":
            raise _RuntimeStatusUnavailable
        # RuntimeIdentity 的 release 模式构造时已同时强制完整 release/base ID。
        _full_id(identity.release_id, "release")
        _full_id(identity.base_id, "base")
        return identity
    if action == "activate":
        release_id = _full_id(args.release_id, "release")
        rollback_anchor = getattr(args, "rollback_anchor", None)
        if rollback_anchor is not None:
            rollback_release_id = _full_id(rollback_anchor, "release")
            return ports.activate_release_with_rollback_anchor(
                _runtime_root(args, ports),
                release_id,
                rollback_release_id,
            )
        return ports.activate_release(_runtime_root(args, ports), release_id)
    if action == "rollback":
        return ports.rollback_release(_runtime_root(args, ports))
    raise RuntimeError("未知运行时动作")


def _json_value(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _json_value(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_json_value(item) for item in value)
    if isinstance(value, Path):
        return str(value)
    return value


def _public_model(model: object) -> dict[str, object]:
    if not dataclasses.is_dataclass(model) or isinstance(model, type):
        raise TypeError("运行时公开函数必须返回类型化模型")
    result = _json_value(model)
    if not isinstance(result, dict):
        raise TypeError("运行时类型化模型必须编码为对象")
    if isinstance(model, RuntimeIdentity):
        # 本地路径属于部署细节；状态只公开可验证的内容身份。
        for field in ("interpreter_realpath", "environment_prefix", "source_root"):
            result.pop(field, None)
    return result


def _emit(payload: Mapping[str, object], *, error: bool = False) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        file=sys.stderr if error else sys.stdout,
        flush=True,
    )


def _emit_lock_progress(progress: object) -> None:
    """stderr 只输出包名与计数，不泄露下载 URL、路径或凭据。"""
    from codev_platform.runtime_lock_download import LockDownloadProgress

    if type(progress) is not LockDownloadProgress:
        raise TypeError("依赖锁进度类型无效")
    if progress.state == "started":
        message = f"依赖制品开始：{progress.package}"
    elif progress.state == "completed":
        message = f"依赖制品完成：{progress.completed}/{progress.total} {progress.package}"
    else:
        message = (
            f"依赖制品处理中：{progress.completed}/{progress.total}，活动任务 {progress.active}"
        )
    print(message, file=sys.stderr, flush=True)


def _object_context(args: argparse.Namespace) -> tuple[str, str] | None:
    if args.runtime_action == "stage":
        kind, value = "base", args.base_id
    elif args.runtime_action in {"verify", "activate"}:
        kind, value = "release", args.release_id
    else:
        return None
    try:
        return kind, require_sha256(value, field=f"{kind}_id")
    except ValueError:
        return None


def _emit_failure(
    args: argparse.Namespace,
    code: str,
    *,
    kind: str | None = None,
    include_context: bool = True,
) -> None:
    payload: dict[str, object] = {
        "error": code,
        "kind": kind or _ACTION_KIND[args.runtime_action],
        "status": "error",
    }
    context = _object_context(args) if include_context else None
    if context is not None:
        object_kind, object_id = context
        payload["kind"] = object_kind
        payload["object_id"] = object_id
    _emit(payload, error=True)


def cmd_runtime(args: argparse.Namespace) -> int:
    """执行一个动作；异常只映射为固定码，不回显底层命令或路径。"""
    ports = _default_ports()
    try:
        model = _dispatch(args, ports)
        _emit(
            {
                "kind": _ACTION_KIND[args.runtime_action],
                "result": _public_model(model),
                "status": "ok",
            }
        )
        return 0
    except _InvalidRuntimeId as error:
        _emit_failure(args, "runtime_id_invalid", kind=error.kind)
    except _RuntimeStatusUnavailable:
        _emit_failure(args, "runtime_status_unavailable")
    except _ManagedRuntimeRootRequired:
        _emit_failure(args, "runtime_root_required")
    except ports.collision_errors:
        has_direct_id = args.runtime_action in {"verify", "activate"}
        _emit_failure(args, "runtime_id_collision", include_context=has_direct_id)
    except ports.operation_errors:
        _emit_failure(args, f"runtime_{args.runtime_action}_failed")
    return 1


def _add_runtime_root(parser: argparse.ArgumentParser, *, child: bool = False) -> None:
    default = argparse.SUPPRESS if child else None
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=default,
        help="版本化运行时受管根；优先于 runtime.release_root 配置",
    )


def register(subparsers) -> None:
    """注册 ``codev-platform runtime`` 的构建动作与唯一生产部署入口。"""
    runtime = subparsers.add_parser("runtime", help="构建、验证并原子切换版本化 WSL 运行时")
    _add_runtime_root(runtime)
    actions = runtime.add_subparsers(dest="runtime_action", required=True)

    lock = actions.add_parser("lock", help="从受批准 freeze 生成 hash lock")
    lock.add_argument("--raw-freeze", type=Path, required=True)
    lock.add_argument("--approved-requirements", type=Path, required=True)
    lock.add_argument("--output", type=Path, required=True)
    lock.add_argument("--wheelhouse", type=Path, required=True)

    base = actions.add_parser("base", help="构建或复用不可变依赖基座")
    base.add_argument("--lock", type=Path, required=True)
    base.add_argument("--approved-requirements", type=Path, required=True)
    base.add_argument("--wheelhouse", type=Path, required=True)

    build = actions.add_parser("build", help="从干净提交构建应用 wheel 候选")
    build.add_argument("--repo", type=Path, required=True)
    build.add_argument("--out-dir", type=Path, required=True)
    build.add_argument(
        "--revision",
        help="部署时指定完整 40 位小写提交号；不切换工作树",
    )
    build.add_argument(
        "--source-user",
        help="仅 Linux root：以受检服务账号读取精确 Git 提交，不放宽 Git 信任",
    )

    stage = actions.add_parser("stage", help="把候选暂存为不可变薄版本")
    stage.add_argument("--candidate", type=Path, required=True)
    stage.add_argument("--wheel", type=Path, required=True)
    stage.add_argument("--base-id", required=True)

    verify = actions.add_parser("verify", help="完整复验一个版本")
    verify.add_argument("release_id")

    status = actions.add_parser("status", help="读取当前解释器邻接的已验证版本身份")
    status.add_argument("--json", action="store_true", help="输出结构化 JSON")

    activate = actions.add_parser("activate", help="原子激活一个已验证版本")
    activate.add_argument("release_id")
    activate.add_argument(
        "--rollback-anchor",
        help="以已验证版本作为回滚锚点，忽略不可验证的历史引用",
    )

    rollback = actions.add_parser("rollback", help="原子回滚到 previous 版本")

    from codev_platform.ops.runtime_promotion import cmd_runtime_promote

    promote = actions.add_parser(
        "promote",
        help="受控前移日常应用薄版本；失败自动恢复原 release",
    )
    promotion_target = promote.add_mutually_exclusive_group(required=True)
    promotion_target.add_argument(
        "--target-release",
        help="已暂存薄 release 的完整 ID；仅用于首次受控 bootstrap",
    )
    promotion_target.add_argument(
        "--target-revision",
        help="fuwuqi/dev tip 的完整 40 位提交；自动降权构建并暂存",
    )
    promote.add_argument("--rollback-anchor", help="bootstrap 模式的 current 回滚锚点")
    promote.add_argument("--repo", type=Path, help="WSL 服务账号拥有的干净源码仓")
    promote.add_argument("--service-user", required=True, help="WSL 非特权服务账号")
    _add_runtime_root(promote, child=True)
    promote.set_defaults(func=cmd_runtime_promote)

    from codev_platform.ops.runtime_access_repair import cmd_runtime_access_repair

    access_repair = actions.add_parser(
        "access-repair-current",
        help="在维护窗口内收敛 current 已完成对象的服务访问投影",
    )
    access_repair.add_argument("--service-user", required=True, help="运行服务的非特权账号")
    confirmation = access_repair.add_mutually_exclusive_group()
    confirmation.add_argument(
        "--dry-run",
        action="store_true",
        help="只验证 current 对象，不修改访问投影（默认）",
    )
    confirmation.add_argument(
        "--yes",
        action="store_true",
        help="确认在已验证维护窗口内执行受控访问投影修复",
    )
    _add_runtime_root(access_repair, child=True)
    access_repair.set_defaults(func=cmd_runtime_access_repair)

    from codev_platform.ops.runtime_staged_release_access import (
        cmd_runtime_staged_release_access,
    )

    staged_access = actions.add_parser(
        "publish-staged-access",
        help="发布已验证暂存 release 的服务账号只读访问投影",
    )
    staged_access.add_argument("release_id", help="尚未激活的完整 release ID")
    staged_access.add_argument("--service-user", required=True, help="运行服务的非特权账号")
    staged_access_confirmation = staged_access.add_mutually_exclusive_group()
    staged_access_confirmation.add_argument(
        "--dry-run",
        action="store_true",
        help="只验证暂存 release，不修改服务访问投影（默认）",
    )
    staged_access_confirmation.add_argument(
        "--yes",
        action="store_true",
        help="确认发布暂存 release 的服务账号访问投影",
    )
    _add_runtime_root(staged_access, child=True)
    staged_access.set_defaults(func=cmd_runtime_staged_release_access)

    from codev_platform.ops.runtime_managed_config_bootstrap import (
        cmd_runtime_managed_config_bootstrap,
    )

    managed_config_bootstrap = actions.add_parser(
        "bootstrap-managed-config",
        help="在维护窗口内一次性补齐缺失的 root 受管机器配置",
    )
    managed_config_bootstrap.add_argument(
        "--service-user",
        required=True,
        help="运行服务的非特权账号",
    )
    bootstrap_confirmation = managed_config_bootstrap.add_mutually_exclusive_group()
    bootstrap_confirmation.add_argument(
        "--dry-run",
        action="store_true",
        help="只验证固定服务账号来源与受管目标（默认）",
    )
    bootstrap_confirmation.add_argument(
        "--yes",
        action="store_true",
        help="确认在已验证维护窗口内执行一次性受管配置引导",
    )
    managed_config_bootstrap.set_defaults(func=cmd_runtime_managed_config_bootstrap)

    from codev_platform.ops.runtime_deploy import cmd_runtime_deploy

    deploy = actions.add_parser("deploy", help="按严格计划执行或续跑正式 WSL 部署")
    deploy.add_argument("--plan", type=Path, required=True, help="绝对路径部署计划 JSON")
    deploy.set_defaults(func=cmd_runtime_deploy)

    for parser in (lock, base, build, stage, verify, status, activate, rollback):
        _add_runtime_root(parser, child=True)
        parser.set_defaults(func=cmd_runtime)


__all__ = ["cmd_runtime", "register"]
