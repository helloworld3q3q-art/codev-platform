"""systemd delegated cgroup v2 文件系统能力与严格原生引用。"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
import subprocess
from pathlib import Path
from typing import Protocol

from codev_platform.core.cgroup_events import parse_cgroup_events
from codev_platform.core.process_tree import run_tree
from codev_platform.reindex.attempt_process import Deadline
from codev_platform.reindex.posix_process_identity import (
    LinuxProcessTable,
    linux_birth_marker,
)

CGROUP_CONTAINMENT_KIND = "cgroup_v2"

_CGROUP_REF_RE = re.compile(
    r"cgroup-v2:v1:(?P<boot>[0-9a-f-]{36}):(?P<name>attempt-[0-9a-f]{64})\Z"
)
_MAX_CONTROL_BYTES = 1024 * 1024
_SYSTEMD_UNIT_RE = re.compile(r"[A-Za-z0-9_.:@\\-]+\.(?:service|scope)\Z")
_SYSTEMD_QUERY_SEC = 2.5
_SYSTEMD_CLEANUP_MAX_SEC = 0.5
_INITIAL_READINESS_SEC = 3.0


def _systemd_delegate_enabled(
    membership: str,
    deadline: Deadline | None = None,
) -> bool:
    """在 xattr 缺失时，向所属 systemd manager 有界查询 Delegate。"""
    if type(membership) is not str or not membership.startswith("/"):
        return False
    components = tuple(part for part in membership.split("/") if part)
    units = tuple(part for part in components if _SYSTEMD_UNIT_RE.fullmatch(part))
    if not units:
        return False
    unit = units[-1]
    user_manager = any(part.startswith("user@") for part in components)
    command = ["/usr/bin/systemctl"]
    if not os.access(command[0], os.X_OK):
        return False
    if user_manager:
        command.append("--user")
    command.extend(("show", unit, "--property=Delegate", "--value", "--no-pager"))
    budget = deadline if deadline is not None else Deadline.start(_SYSTEMD_QUERY_SEC)
    if not isinstance(budget, Deadline):
        raise ValueError("systemd delegation deadline 无效")
    remaining = budget.remaining()
    if remaining <= 0:
        return False
    cleanup_timeout = min(_SYSTEMD_CLEANUP_MAX_SEC, remaining * 0.2)
    command_timeout = remaining - cleanup_timeout
    if command_timeout <= 0 or cleanup_timeout <= 0:
        return False
    try:
        completed = run_tree(
            command,
            capture_output=True,
            check=False,
            timeout=command_timeout,
            cleanup_timeout_sec=cleanup_timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return not budget.expired() and completed.returncode == 0 and completed.stdout.strip() == b"yes"


class CgroupDelegationError(RuntimeError):
    """当前进程没有可证明的 systemd cgroup v2 委派。"""


def _validate_boot_id(boot_id: str) -> str:
    linux_birth_marker(boot_id, 1)
    return boot_id


def _attempt_name(attempt_id: str) -> str:
    if type(attempt_id) is not str or not attempt_id.strip():
        raise ValueError("attempt_id 无效")
    try:
        encoded = attempt_id.encode("utf-8")
    except UnicodeError:
        raise ValueError("attempt_id 不是有效 UTF-8") from None
    if len(encoded) > 4096:
        raise ValueError("attempt_id 超过长度上限")
    return f"attempt-{hashlib.sha256(encoded).hexdigest()}"


def encode_cgroup_native_ref(boot_id: str, attempt_id: str) -> str:
    return f"cgroup-v2:v1:{_validate_boot_id(boot_id)}:{_attempt_name(attempt_id)}"


def parse_cgroup_native_ref(native_ref: str) -> tuple[str, str]:
    match = _CGROUP_REF_RE.fullmatch(native_ref)
    if match is None:
        raise ValueError("cgroup 原生引用无效")
    return _validate_boot_id(match["boot"]), match["name"]


class CgroupFilesystem(Protocol):
    root: Path
    boot_id: str

    def assert_ready(self, deadline: Deadline) -> None: ...
    def create_attempt(self, attempt_id: str) -> str: ...
    def path_for(self, native_ref: str) -> Path: ...
    def contains_pid(self, native_ref: str, pid: int) -> bool: ...
    def populated(self, native_ref: str) -> bool: ...
    def kill(self, native_ref: str) -> None: ...
    def exists(self, native_ref: str) -> bool: ...
    def exists_attempt(self, attempt_id: str) -> bool: ...
    def remove_empty(self, native_ref: str) -> None: ...


def _read_limited(path: Path, limit: int = _MAX_CONTROL_BYTES) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("cgroup 控制文件超过长度上限")
    return data


def _ensure_readiness_budget(
    deadline: Deadline | None,
    stage: str,
) -> None:
    if deadline is None:
        return
    if not isinstance(deadline, Deadline):
        raise ValueError("cgroup readiness deadline 无效")
    if deadline.expired():
        raise CgroupDelegationError(f"cgroup readiness 预算已耗尽：{stage}")


def _remove_probe_child(child: Path) -> BaseException | None:
    try:
        child.rmdir()
    except BaseException as exc:
        return exc
    return None


def _raise_probe_failure(
    failure: BaseException | None,
    cleanup_error: BaseException | None,
) -> None:
    for error in (failure, cleanup_error):
        if isinstance(error, MemoryError):
            raise error
        if error is not None and not isinstance(error, Exception):
            raise error
    if cleanup_error is not None:
        raise CgroupDelegationError("委派探针子组无法清理") from cleanup_error
    if isinstance(failure, CgroupDelegationError):
        raise failure
    if failure is not None:
        raise CgroupDelegationError("attempt 子组或 cgroup.kill 不可用") from failure


class SystemdCgroupV2:
    """只管理当前 systemd 委派根之下的直接 attempt 子组。"""

    def __init__(
        self,
        *,
        mount_root: Path = Path("/sys/fs/cgroup"),
        proc_cgroup_path: Path = Path("/proc/self/cgroup"),
        boot_id: str | None = None,
    ) -> None:
        self.boot_id = _validate_boot_id(
            boot_id if boot_id is not None else LinuxProcessTable().boot_id
        )
        self._mount = Path(mount_root).resolve(strict=True)
        self.root = self._discover_root(proc_cgroup_path)
        self.assert_ready(Deadline.start(_INITIAL_READINESS_SEC))

    def assert_ready(self, deadline: Deadline) -> None:
        """不启动业务进程，证明 delegation 与 cgroup.kill 控制面。"""
        if not isinstance(deadline, Deadline):
            raise ValueError("cgroup readiness deadline 无效")
        _ensure_readiness_budget(deadline, "开始")
        self._validate_delegation(deadline)
        _ensure_readiness_budget(deadline, "delegation 校验后")
        self._probe_child_kill(deadline)
        _ensure_readiness_budget(deadline, "cgroup.kill 校验后")

    def _discover_root(self, proc_cgroup_path: Path) -> Path:
        raw = _read_limited(Path(proc_cgroup_path), 4096)
        try:
            lines = raw.decode("ascii").splitlines()
        except UnicodeError:
            raise CgroupDelegationError("cgroup membership 不是 ASCII") from None
        unified = [line[3:] for line in lines if line.startswith("0::")]
        if len(unified) != 1 or not unified[0].startswith("/"):
            raise CgroupDelegationError("当前进程不在唯一 cgroup v2 层级")
        self._membership = unified[0]
        root = (self._mount / unified[0].lstrip("/")).resolve(strict=True)
        if root == self._mount or not root.is_relative_to(self._mount):
            raise CgroupDelegationError("委派根路径无效")
        return root

    def _validate_delegation(self, deadline: Deadline | None = None) -> None:
        required = (
            self.root / "cgroup.controllers",
            self.root / "cgroup.events",
            self.root / "cgroup.procs",
            self.root / "cgroup.type",
        )
        if not all(path.is_file() for path in required):
            raise CgroupDelegationError("cgroup v2 核心文件不完整")
        _ensure_readiness_budget(deadline, "核心文件校验后")
        try:
            delegate_xattr = os.getxattr(self.root, "user.delegate")
        except OSError:
            delegate_xattr = None
        _ensure_readiness_budget(deadline, "delegation xattr 校验后")
        xattr_valid = delegate_xattr in {None, b"1"}
        if deadline is None:
            delegated = xattr_valid and _systemd_delegate_enabled(self._membership)
        else:
            delegated = xattr_valid and _systemd_delegate_enabled(
                self._membership,
                deadline,
            )
        writable = os.access(self.root, os.W_OK | os.X_OK) and os.access(
            self.root / "cgroup.procs", os.W_OK
        )
        _ensure_readiness_budget(deadline, "delegation 权限校验后")
        if not delegated or not writable:
            raise CgroupDelegationError("systemd 未提供可写 delegation")
        if _read_limited(self.root / "cgroup.type", 128).strip() != b"domain":
            raise CgroupDelegationError("委派根不是 domain cgroup")
        _ensure_readiness_budget(deadline, "delegation 类型校验后")

    def _probe_child_kill(self, deadline: Deadline | None = None) -> None:
        name = f"reindex-probe-{os.getpid()}-{secrets.token_hex(8)}"
        child = self.root / name
        created = False
        failure: BaseException | None = None
        try:
            _ensure_readiness_budget(deadline, "探针子组创建前")
            child.mkdir(mode=0o700)
            created = True
            _ensure_readiness_budget(deadline, "探针子组创建后")
            self._verify_child(child, deadline)
            _ensure_readiness_budget(deadline, "cgroup.kill 校验后")
        except BaseException as exc:
            failure = exc
        cleanup_error = _remove_probe_child(child) if created else None
        _raise_probe_failure(failure, cleanup_error)

    @staticmethod
    def _verify_child(child: Path, deadline: Deadline | None = None) -> None:
        _ensure_readiness_budget(deadline, "子组类型读取前")
        if _read_limited(child / "cgroup.type", 128).strip() != b"domain":
            raise ValueError("attempt 子组不是 domain")
        _ensure_readiness_budget(deadline, "子组事件读取前")
        if parse_cgroup_events(_read_limited(child / "cgroup.events")):
            raise ValueError("新建 attempt 子组不是空组")
        _ensure_readiness_budget(deadline, "cgroup.kill 打开前")
        descriptor = os.open(
            child / "cgroup.kill",
            os.O_WRONLY | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            _ensure_readiness_budget(deadline, "cgroup.kill 打开后")
        finally:
            os.close(descriptor)
        _ensure_readiness_budget(deadline, "cgroup.kill 关闭后")

    def create_attempt(self, attempt_id: str) -> str:
        native_ref = encode_cgroup_native_ref(self.boot_id, attempt_id)
        child = self.path_for(native_ref)
        child.mkdir(mode=0o700)
        try:
            self._verify_child(child)
        except (OSError, ValueError):
            child.rmdir()
            raise CgroupDelegationError("attempt cgroup 创建后验证失败") from None
        return native_ref

    def path_for(self, native_ref: str) -> Path:
        boot_id, name = parse_cgroup_native_ref(native_ref)
        if boot_id != self.boot_id:
            raise ValueError("cgroup 引用属于其他 Linux 启动周期")
        child = self.root / name
        if child.parent != self.root:
            raise ValueError("attempt cgroup 路径越界")
        return child

    def path_for_attempt(self, attempt_id: str) -> Path:
        return self.root / _attempt_name(attempt_id)

    def contains_pid(self, native_ref: str, pid: int) -> bool:
        raw = _read_limited(self.path_for(native_ref) / "cgroup.procs")
        try:
            members = {int(value) for value in raw.split()}
        except ValueError:
            raise ValueError("cgroup.procs 含非整数 PID") from None
        return pid in members

    def populated(self, native_ref: str) -> bool:
        return parse_cgroup_events(_read_limited(self.path_for(native_ref) / "cgroup.events"))

    def kill(self, native_ref: str) -> None:
        path = self.path_for(native_ref) / "cgroup.kill"
        descriptor = os.open(path, os.O_WRONLY | getattr(os, "O_CLOEXEC", 0))
        try:
            os.write(descriptor, b"1")
        finally:
            os.close(descriptor)

    def exists(self, native_ref: str) -> bool:
        try:
            mode = self.path_for(native_ref).stat().st_mode
        except FileNotFoundError:
            return False
        except ValueError:
            return False
        return stat.S_ISDIR(mode)

    def exists_attempt(self, attempt_id: str) -> bool:
        try:
            mode = self.path_for_attempt(attempt_id).stat().st_mode
        except FileNotFoundError:
            return False
        return stat.S_ISDIR(mode)

    def remove_empty(self, native_ref: str) -> None:
        if self.populated(native_ref):
            raise OSError("attempt cgroup 仍有存活进程")
        self.path_for(native_ref).rmdir()


__all__ = [
    "CGROUP_CONTAINMENT_KIND",
    "CgroupDelegationError",
    "CgroupFilesystem",
    "SystemdCgroupV2",
    "encode_cgroup_native_ref",
    "parse_cgroup_events",
    "parse_cgroup_native_ref",
]
