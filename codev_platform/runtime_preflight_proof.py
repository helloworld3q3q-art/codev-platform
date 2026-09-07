"""目标用户执行的 systemd unit 与运行权限规范证明。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections.abc import Callable, Sequence
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from codev_platform.core.config import load_config
from codev_platform.core.runtime_identity import runtime_identity
from codev_platform.core.runtime_models import RuntimeIdentity, SystemdRuntime
from codev_platform.mcp_systemd import render_managed_systemd_units
from codev_platform.mcp_systemd_install_contract import (
    SystemdRuntimeBinding,
    require_runtime_revision,
)
from codev_platform.mcp_systemd_unit_registry import MANAGED_SYSTEMD_UNIT_NAMES
from codev_platform.runtime_preflight import (
    permission_requirements,
    probe_current_user_until,
)
from codev_platform.runtime_preflight_contract import (
    DEFAULT_PROBE_TIMEOUT_SEC,
    ProbeDeadline,
)
from codev_platform.runtime_service_process import resolve_service_account


_ERROR_CODES = frozenset(
    {
        "invalid_arguments",
        "runtime_identity_mismatch",
        "target_user_mismatch",
        "preflight_timeout",
        "preflight_failed",
    }
)


class TargetUserPreflightError(RuntimeError):
    """只携带固定错误码的目标用户预检失败。"""

    def __init__(self, code: str) -> None:
        if code not in _ERROR_CODES:
            raise ValueError("目标用户预检错误码不受支持")
        self.code = code
        super().__init__(f"目标用户预检失败：{code}")


class _CliArgumentsError(ValueError):
    """CLI 参数形状无效；不保存 argparse 的原始错误正文。"""


class _StrictParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise _CliArgumentsError("目标用户预检参数无效")


class _StoreOnce(argparse.Action):
    """拒绝重复选项，避免同一身份字段出现覆盖语义。"""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        del parser, option_string
        if getattr(namespace, self.dest, None) is not None:
            raise _CliArgumentsError("目标用户预检参数重复")
        setattr(namespace, self.dest, values)


class _DiscardText:
    """有界丢弃依赖输出，保证机器协议不混入日志或敏感值。"""

    def write(self, value: str) -> int:
        return len(value)

    def flush(self) -> None:
        return None


def create_target_user_systemd_proof(
    *,
    release_root: str,
    expected_release_id: str,
    expected_runtime_revision: str,
    target_user: str,
    timeout_sec: float = DEFAULT_PROBE_TIMEOUT_SEC,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    """绑定目标 release/用户，用一份配置生成 unit 摘要并执行权限探针。"""
    runtime, release_id, revision, user = _validated_request(
        release_root,
        expected_release_id,
        expected_runtime_revision,
        target_user,
    )
    try:
        deadline = ProbeDeadline.after(timeout_sec, monotonic)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise TargetUserPreflightError("invalid_arguments") from None

    try:
        identity = runtime_identity()
        _verify_release_identity(identity, runtime, release_id, revision)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise TargetUserPreflightError("runtime_identity_mismatch") from None
    _ensure_deadline(deadline)

    try:
        current_user = _current_user_name()
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise TargetUserPreflightError("target_user_mismatch") from None
    if current_user != user:
        raise TargetUserPreflightError("target_user_mismatch")
    _ensure_deadline(deadline)

    try:
        cfg = load_config()
        if type(cfg) is not dict:
            raise TypeError("平台配置快照必须是字典")
        units = render_managed_systemd_units(
            cfg,
            user,
            runtime=runtime,
            runtime_binding=SystemdRuntimeBinding(
                release_root=runtime.release_root.as_posix(),
                expected_release_id=release_id,
                target_user=user,
            ),
            working_directory=_target_user_working_directory(user),
        )
        unit_proofs = _unit_proofs(units)
        _ensure_deadline(deadline)
        requirements = permission_requirements(cfg, runtime)
        probe_current_user_until(
            requirements,
            deadline,
        )
        _ensure_deadline(deadline)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except TargetUserPreflightError:
        raise
    except Exception:
        raise TargetUserPreflightError("preflight_failed") from None

    return {
        "release_id": release_id,
        "runtime_revision": revision,
        "schema_version": 1,
        "target_user": user,
        "units": unit_proofs,
    }


def _validated_request(
    release_root: object,
    release_id: object,
    runtime_revision: object,
    target_user: object,
) -> tuple[SystemdRuntime, str, str, str]:
    try:
        binding = SystemdRuntimeBinding(
            release_root=release_root,  # type: ignore[arg-type]
            expected_release_id=release_id,  # type: ignore[arg-type]
            target_user=target_user,  # type: ignore[arg-type]
        )
        revision = require_runtime_revision(runtime_revision)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise TargetUserPreflightError("invalid_arguments") from None
    return (
        SystemdRuntime(Path(binding.release_root)),
        binding.expected_release_id,
        revision,
        binding.target_user,
    )


def _verify_release_identity(
    identity: object,
    runtime: SystemdRuntime,
    expected_release_id: str,
    expected_runtime_revision: str,
) -> None:
    """证明运行模式、版本、current 指针、解释器和环境前缀完全一致。"""
    if type(identity) is not RuntimeIdentity:
        raise ValueError("运行身份类型无效")
    root = runtime.release_root
    try:
        releases_root = (root / "releases").resolve(strict=True)
        expected_release = (releases_root / expected_release_id).resolve(strict=True)
        current_release = (root / "current").resolve(strict=True)
        expected_prefix = (root / "current" / "venv").resolve(strict=True)
        expected_python = runtime.python.resolve(strict=True)
        actual_prefix = Path(identity.environment_prefix).resolve(strict=True)
        actual_python = Path(identity.interpreter_realpath).resolve(strict=True)
    except OSError:
        raise ValueError("运行身份路径无法证明") from None
    if (
        identity.mode != "release"
        or identity.release_id != expected_release_id
        or identity.runtime_revision != expected_runtime_revision
        or identity.source_root is not None
        or expected_release.parent != releases_root
        or current_release != expected_release
        or actual_prefix != expected_prefix
        or actual_python != expected_python
    ):
        raise ValueError("运行身份与目标 release 不一致")


def _current_user_name() -> str:
    """从当前有效 UID 反查唯一用户名。"""
    import pwd

    name = pwd.getpwuid(os.geteuid()).pw_name
    if type(name) is not str or not name:
        raise OSError("当前 UID 用户名不可用")
    return name


def _target_user_working_directory(user: str) -> str:
    """从受控服务账号记录取得与安装清单一致的绝对工作目录。"""
    try:
        account = resolve_service_account(user)
        if account.name != user or not account.home.is_absolute():
            raise ValueError("服务账号主目录身份不一致")
        return account.home.as_posix()
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise ValueError("目标用户工作目录无效") from None


def _unit_proofs(units: object) -> list[dict[str, str]]:
    if (
        len(MANAGED_SYSTEMD_UNIT_NAMES) != 12
        or type(units) is not dict
        or frozenset(units) != MANAGED_SYSTEMD_UNIT_NAMES
    ):
        raise ValueError("受管 systemd unit 集合漂移")
    proofs: list[dict[str, str]] = []
    for name in sorted(MANAGED_SYSTEMD_UNIT_NAMES):
        content = units[name]
        if type(content) is not str:
            raise TypeError("受管 systemd unit 内容必须是字符串")
        proofs.append(
            {
                "name": name,
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
        )
    return proofs


def _ensure_deadline(deadline: ProbeDeadline) -> None:
    try:
        deadline.ensure()
    except TimeoutError:
        raise TargetUserPreflightError("preflight_timeout") from None


def _parser() -> argparse.ArgumentParser:
    parser = _StrictParser(
        prog="python -m codev_platform.runtime_preflight_proof",
        add_help=False,
        allow_abbrev=False,
    )
    for option in (
        "release-root",
        "expected-release-id",
        "expected-runtime-revision",
        "target-user",
    ):
        parser.add_argument(f"--{option}", required=True, action=_StoreOnce)
    return parser


def _emit(payload: dict[str, object]) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """解析严格机器参数；成功输出 proof，失败只输出固定码。"""
    try:
        args = _parser().parse_args(argv)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        _emit({"error_code": "invalid_arguments", "schema_version": 1})
        return 2
    try:
        sink = _DiscardText()
        with redirect_stdout(sink), redirect_stderr(sink):
            payload = create_target_user_systemd_proof(
                release_root=args.release_root,
                expected_release_id=args.expected_release_id,
                expected_runtime_revision=args.expected_runtime_revision,
                target_user=args.target_user,
            )
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except TargetUserPreflightError as error:
        _emit({"error_code": error.code, "schema_version": 1})
        return 2 if error.code == "invalid_arguments" else 1
    except Exception:
        _emit({"error_code": "preflight_failed", "schema_version": 1})
        return 1
    _emit(payload)
    return 0


if __name__ == "__main__":  # pragma: no cover - 由 python -m 入口执行
    raise SystemExit(main())


__all__ = [
    "TargetUserPreflightError",
    "create_target_user_systemd_proof",
    "main",
]
