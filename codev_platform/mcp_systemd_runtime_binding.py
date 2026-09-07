"""systemd 安装事务对 current release 的共享锁绑定与复证。"""

from __future__ import annotations

from pathlib import Path

from codev_platform.mcp_systemd_install_contract import (
    SystemdInstallManifest,
    SystemdInstallTransactionError,
)
from codev_platform.runtime_release_binding import BoundRelease, bind_current_release


def bind_manifest_runtime(manifest: SystemdInstallManifest):
    """按 manifest 期望身份持有 current 的共享激活锁和内容锁。"""
    binding = manifest.runtime_binding
    if binding is None:
        raise SystemdInstallTransactionError("systemd 安装清单缺少 release 运行时绑定")
    return bind_current_release(
        Path(binding.release_root),
        binding.expected_release_id,
        manifest.runtime_revision,
    )


def verify_bound_manifest_runtime(
    manifest: SystemdInstallManifest,
    bound: BoundRelease,
) -> None:
    """证明 manifest、执行解释器和锁内 current 属于同一 release。"""
    binding = manifest.runtime_binding
    if (
        binding is None
        or type(bound) is not BoundRelease
        or bound.root.as_posix() != binding.release_root
        or bound.release_id != binding.expected_release_id
        or bound.runtime_revision != manifest.runtime_revision
        or bound.interpreter_path.as_posix() != binding.immutable_python.as_posix()
    ):
        raise SystemdInstallTransactionError("systemd 安装运行时绑定不一致")


__all__ = ["bind_manifest_runtime", "verify_bound_manifest_runtime"]
