"""缺失 root 受管配置的一次性维护窗口引导。"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

from codev_platform.core.runtime_models import require_sha256
from codev_platform.runtime_managed_configuration import (
    MAX_MANAGED_CONFIG_BYTES,
    ManagedConfigurationBootstrapReceipt,
    bootstrap_missing_managed_configuration,
)
from codev_platform.runtime_service_process import ServiceAccount


class ManagedConfigurationBootstrapError(RuntimeError):
    """受管配置 bootstrap 未能保持可证明的受控边界。"""


@dataclass(frozen=True, slots=True)
class ManagedConfigurationBootstrapPorts:
    """将维护状态机、来源读取和发布端口隔离为可替换的窄契约。"""

    resolve_service_account: Callable[[str], ServiceAccount]
    maintenance_permit: Callable[[], AbstractContextManager[bool]]
    inspect_maintenance: Callable[[], None]
    read_service_configuration: Callable[[ServiceAccount], bytes]
    publish_configuration: Callable[..., ManagedConfigurationBootstrapReceipt]


def bootstrap_service_managed_configuration(
    *,
    service_user: str,
    apply: bool,
    ports: ManagedConfigurationBootstrapPorts | None = None,
) -> ManagedConfigurationBootstrapReceipt:
    """默认只验证；写入必须持有维护管理员许可并做前后状态证明。"""
    if type(service_user) is not str or not service_user or type(apply) is not bool:
        raise ManagedConfigurationBootstrapError("受管配置引导参数无效")
    active_ports = _default_ports() if ports is None else ports
    _require_ports(active_ports)
    account = _resolve_service_account(active_ports, service_user)
    if not apply:
        return _bootstrap_once(active_ports, account, apply=False)
    with active_ports.maintenance_permit() as permitted:
        if permitted is not True:
            raise ManagedConfigurationBootstrapError("受管配置引导维护窗口未证明")
        active_ports.inspect_maintenance()
        receipt = _bootstrap_once(active_ports, account, apply=True)
        active_ports.inspect_maintenance()
        return receipt


def read_service_managed_configuration(account: ServiceAccount) -> bytes:
    """在 Linux root 上以稳定快照读取固定服务账号的用户级 JSON。"""
    _require_linux_root()
    if type(account) is not ServiceAccount:
        raise ManagedConfigurationBootstrapError("服务账号身份无效")
    config_directory = account.home / ".codev-platform"
    source = config_directory / "config.json"
    _require_root_owned_ancestor_chain(account.home)
    _require_service_directory(account.home, account.uid)
    _require_service_directory(config_directory, account.uid)
    try:
        metadata = source.lstat()
    except OSError as error:
        raise ManagedConfigurationBootstrapError("服务账号机器配置不可用") from error
    if not _trusted_service_file(metadata, account.uid):
        raise ManagedConfigurationBootstrapError("服务账号机器配置不受信任")
    return _read_stable_file(source, metadata)


def _bootstrap_once(
    ports: ManagedConfigurationBootstrapPorts,
    account: ServiceAccount,
    *,
    apply: bool,
) -> ManagedConfigurationBootstrapReceipt:
    try:
        config = ports.read_service_configuration(account)
        receipt = ports.publish_configuration(
            config,
            service_user=account.name,
            apply=apply,
        )
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except ManagedConfigurationBootstrapError:
        raise
    except Exception as error:
        raise ManagedConfigurationBootstrapError("受管配置引导无法完成") from error
    return _require_receipt(receipt)


def _resolve_service_account(
    ports: ManagedConfigurationBootstrapPorts,
    service_user: str,
) -> ServiceAccount:
    try:
        account = ports.resolve_service_account(service_user)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise ManagedConfigurationBootstrapError("服务账号无法解析") from error
    if type(account) is not ServiceAccount or account.name != service_user:
        raise ManagedConfigurationBootstrapError("服务账号身份无效")
    return account


def _require_ports(ports: ManagedConfigurationBootstrapPorts) -> None:
    if not isinstance(ports, ManagedConfigurationBootstrapPorts) or not all(
        callable(value)
        for value in (
            ports.resolve_service_account,
            ports.maintenance_permit,
            ports.inspect_maintenance,
            ports.read_service_configuration,
            ports.publish_configuration,
        )
    ):
        raise ManagedConfigurationBootstrapError("受管配置引导适配器不可用")


def _require_receipt(value: object) -> ManagedConfigurationBootstrapReceipt:
    if type(value) is not ManagedConfigurationBootstrapReceipt or value.state not in {
        "ready",
        "published",
        "already_published",
    }:
        raise ManagedConfigurationBootstrapError("受管配置引导回执无效")
    try:
        require_sha256(value.config_sha256, field="config_sha256")
        require_sha256(value.environment_sha256, field="environment_sha256")
        require_sha256(value.evidence_sha256, field="evidence_sha256")
    except ValueError:
        raise ManagedConfigurationBootstrapError("受管配置引导回执无效") from None
    return value


def _require_service_directory(path: Path, expected_uid: int) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ManagedConfigurationBootstrapError("服务账号机器配置目录不可用") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise ManagedConfigurationBootstrapError("服务账号机器配置目录不受信任")


def _require_root_owned_ancestor_chain(home: Path) -> None:
    """服务账号可控制 HOME 本身，但不能让其他账号替换其父路径。"""
    current = home.parent
    while True:
        try:
            metadata = current.lstat()
        except OSError as error:
            raise ManagedConfigurationBootstrapError("服务账号机器配置祖先目录不可用") from error
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise ManagedConfigurationBootstrapError("服务账号机器配置祖先目录不受信任")
        if current == current.parent:
            return
        current = current.parent


def _trusted_service_file(metadata: os.stat_result, expected_uid: int) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and metadata.st_uid == expected_uid
        and stat.S_IMODE(metadata.st_mode) & 0o022 == 0
        and metadata.st_nlink == 1
        and 0 < metadata.st_size <= MAX_MANAGED_CONFIG_BYTES
    )


def _read_stable_file(path: Path, linked: os.stat_result) -> bytes:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if _identity(linked) != _identity(opened):
            raise ManagedConfigurationBootstrapError("服务账号机器配置读取前发生替换")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(8192, MAX_MANAGED_CONFIG_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_MANAGED_CONFIG_BYTES:
                raise ManagedConfigurationBootstrapError("服务账号机器配置超出固定上限")
        after = os.fstat(descriptor)
        if _identity(opened) != _identity(after) or total != after.st_size:
            raise ManagedConfigurationBootstrapError("服务账号机器配置读取期间发生变化")
        return b"".join(chunks)
    except ManagedConfigurationBootstrapError:
        raise
    except OSError as error:
        raise ManagedConfigurationBootstrapError("服务账号机器配置无法安全读取") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _require_linux_root() -> None:
    if os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise ManagedConfigurationBootstrapError("受管配置引导要求 Linux root")


def _default_ports() -> ManagedConfigurationBootstrapPorts:
    from codev_platform.ops.reindex_maintenance import inspect_reindex_maintenance
    from codev_platform.reindex.maintenance_gate_state import maintenance_admin_window_permit
    from codev_platform.runtime_service_process import resolve_service_account

    return ManagedConfigurationBootstrapPorts(
        resolve_service_account=resolve_service_account,
        maintenance_permit=maintenance_admin_window_permit,
        inspect_maintenance=inspect_reindex_maintenance,
        read_service_configuration=read_service_managed_configuration,
        publish_configuration=bootstrap_missing_managed_configuration,
    )


__all__ = [
    "ManagedConfigurationBootstrapError",
    "ManagedConfigurationBootstrapPorts",
    "bootstrap_service_managed_configuration",
    "read_service_managed_configuration",
]
