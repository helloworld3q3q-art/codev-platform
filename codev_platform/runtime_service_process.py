"""服务账号身份与唯一最小权限进程启动命令。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any

from codev_platform.runtime_execution_trust import verify_root_controlled_executable


_MAX_POSIX_ID = 2**32 - 2
_SET_PRIV = "/usr/bin/setpriv"
_ENV = "/usr/bin/env"
_ACCOUNT_NAME = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
_ENVIRONMENT_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class RuntimeServiceProcessError(RuntimeError):
    """服务账号或最小权限启动契约不成立。"""


@dataclass(frozen=True, slots=True)
class ServiceAccount:
    """经过完整形状校验的非特权 POSIX 服务账号。"""

    name: str
    uid: int
    gid: int
    home: Path

    def __post_init__(self) -> None:
        if not _valid_account_name(self.name) or not _valid_posix_id(self.uid):
            raise RuntimeServiceProcessError("服务账号身份无效")
        if not _valid_posix_id(self.gid) or not _valid_home(self.home):
            raise RuntimeServiceProcessError("服务账号身份无效")


def resolve_service_account(user: str) -> ServiceAccount:
    """从系统账号库解析并冻结唯一非特权服务身份。"""
    selected_user = validate_service_account_name(user)
    failed = False
    result: ServiceAccount | None = None
    try:
        record = _lookup_account(selected_user)
        if record.pw_name != selected_user:
            raise RuntimeServiceProcessError("服务账号名称与系统记录不一致")
        result = ServiceAccount(
            name=record.pw_name,
            uid=record.pw_uid,
            gid=record.pw_gid,
            home=Path(record.pw_dir),
        )
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        failed = True
    if failed or result is None:
        raise RuntimeServiceProcessError("服务账号不存在或身份无效")
    return result


def validate_service_account_name(value: object) -> str:
    """在查询账号库或构造路径前验证服务账号词法名称。"""
    if not _valid_account_name(value):
        raise RuntimeServiceProcessError("服务账号名称无效")
    return value


def build_service_process_argv(
    account: ServiceAccount,
    command: tuple[str, ...],
    environment: dict[str, str],
) -> tuple[str, ...]:
    """构造固定 setpriv + env -i 启动链；不提供可扩大的策略参数。"""
    if type(account) is not ServiceAccount or not _valid_command(command):
        raise RuntimeServiceProcessError("服务进程启动参数无效")
    assignments = _environment_assignments(environment)
    _require_linux_root()
    setpriv, env = _verified_fixed_launchers()
    return (
        setpriv.as_posix(),
        f"--reuid={account.uid}",
        f"--regid={account.gid}",
        "--clear-groups",
        "--no-new-privs",
        "--bounding-set=-all",
        "--inh-caps=-all",
        "--ambient-caps=-all",
        env.as_posix(),
        "-i",
        *assignments,
        *command,
    )


def _lookup_account(user: str) -> Any:
    import pwd

    return pwd.getpwnam(user)


def _valid_account_name(value: object) -> bool:
    return type(value) is str and _ACCOUNT_NAME.fullmatch(value) is not None and value != "root"


def _valid_posix_id(value: object) -> bool:
    return type(value) is int and 0 < value <= _MAX_POSIX_ID


def _valid_home(value: object) -> bool:
    if not isinstance(value, Path):
        return False
    text = value.as_posix()
    candidate = PurePosixPath(text)
    return (
        text.startswith("/")
        and not text.startswith("//")
        and text != "/"
        and "\x00" not in text
        and ".." not in candidate.parts
        and candidate.as_posix() == text
        and (os.name != "posix" or Path(text).resolve(strict=False).as_posix() == text)
    )


def _valid_command(command: object) -> bool:
    if type(command) is not tuple or not command:
        return False
    if any(type(argument) is not str or "\x00" in argument for argument in command):
        return False
    executable = PurePosixPath(command[0])
    return (
        executable.is_absolute()
        and command[0].startswith("/")
        and not command[0].startswith("//")
        and ".." not in executable.parts
        and executable.as_posix() == command[0]
    )


def _environment_assignments(environment: dict[str, str]) -> tuple[str, ...]:
    if type(environment) is not dict:
        raise RuntimeServiceProcessError("服务进程环境无效")
    if any(
        type(key) is not str
        or _ENVIRONMENT_KEY.fullmatch(key) is None
        or type(value) is not str
        or "\x00" in value
        for key, value in environment.items()
    ):
        raise RuntimeServiceProcessError("服务进程环境无效")
    return tuple(f"{key}={value}" for key, value in sorted(environment.items()))


def _require_linux_root() -> None:
    if (
        os.name != "posix"
        or not sys.platform.startswith("linux")
        or not hasattr(os, "geteuid")
        or os.geteuid() != 0
    ):
        raise RuntimeServiceProcessError("服务进程启动只支持 Linux root")


def _verified_fixed_launchers() -> tuple[Path, Path]:
    failed = False
    result: tuple[Path, Path] | None = None
    try:
        selected = tuple(
            verify_root_controlled_executable(Path(value)) for value in (_SET_PRIV, _ENV)
        )
        if len(selected) != 2 or any(not isinstance(path, Path) for path in selected):
            raise RuntimeServiceProcessError("服务进程固定启动器不受信任")
        result = (selected[0], selected[1])
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        failed = True
    if failed or result is None:
        raise RuntimeServiceProcessError("服务进程固定启动器不受信任")
    return result


__all__ = [
    "RuntimeServiceProcessError",
    "ServiceAccount",
    "build_service_process_argv",
    "resolve_service_account",
    "validate_service_account_name",
]
