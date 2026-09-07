"""锁 capability 在 fork 后的 descriptor 清理回归。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import codev_platform.runtime_deployment_lock_capability as deployment_capability
import codev_platform.runtime_object_lock_capability as object_capability
from codev_platform.runtime_root_binding import RuntimeRootBinding
from codev_platform.runtime_storage import deployment_lock_at, id_lock_at


_BASE_ID = "1" * 64
_POSIX = os.name == "posix"


@pytest.mark.skipif(
    not _POSIX or not hasattr(os, "fork"),
    reason="fork 锁 descriptor 回归仅在 WSL/Linux 验证",
)
def test已吊销部署锁能力不关闭复用descriptor(tmp_path: Path) -> None:
    """已离开 deployment lock 的 capability 不得影响后续复用 fd。"""
    root = tmp_path / "runtime"
    root.mkdir()
    with RuntimeRootBinding(root, os.geteuid()).bind() as bound_root:
        with deployment_lock_at(bound_root) as capability:
            state = deployment_capability._CAPABILITIES[capability]
            descriptor = state.descriptor
        assert capability not in deployment_capability._CAPABILITIES
        deployment_capability._CAPABILITIES[capability] = state
        try:
            _assert_fork_keeps_reused_descriptor_open(descriptor)
        finally:
            deployment_capability._CAPABILITIES.pop(capability, None)


@pytest.mark.skipif(
    not _POSIX or not hasattr(os, "fork"),
    reason="fork 锁 descriptor 回归仅在 WSL/Linux 验证",
)
def test已吊销对象锁能力不关闭复用descriptor(tmp_path: Path) -> None:
    """已离开对象锁的 capability 不得影响后续复用 fd。"""
    root = tmp_path / "runtime"
    root.mkdir()
    with RuntimeRootBinding(root, os.geteuid()).bind() as bound_root:
        with id_lock_at(bound_root, "base", _BASE_ID, shared=False) as capability:
            state = object_capability._CAPABILITIES[capability]
            descriptor = state.descriptor
        assert capability not in object_capability._CAPABILITIES
        object_capability._CAPABILITIES[capability] = state
        try:
            _assert_fork_keeps_reused_descriptor_open(descriptor)
        finally:
            object_capability._CAPABILITIES.pop(capability, None)


def _assert_fork_keeps_reused_descriptor_open(stale_descriptor: int) -> None:
    """将已关闭编号复用为无关 fd，证明 child at-fork 不会误关它。"""
    probe = os.open(os.devnull, os.O_RDONLY)
    result_read: int | None = None
    result_write: int | None = None
    child_pid: int | None = None
    try:
        if probe != stale_descriptor:
            os.dup2(probe, stale_descriptor)
            os.close(probe)
            probe = stale_descriptor
        result_read, result_write = os.pipe()
        child_pid = os.fork()
        if child_pid == 0:
            os.close(result_read)
            try:
                os.fstat(probe)
            except OSError:
                result = b"closed"
            else:
                result = b"open"
            os.write(result_write, result)
            os.close(result_write)
            os._exit(0)
        os.close(result_write)
        result_write = None
        assert os.read(result_read, 16) == b"open"
        os.close(result_read)
        result_read = None
        _pid, status = os.waitpid(child_pid, 0)
        child_pid = None
        assert os.waitstatus_to_exitcode(status) == 0
    finally:
        for descriptor in (probe, result_read, result_write):
            if descriptor is None:
                continue
            try:
                os.close(descriptor)
            except OSError:
                pass
        if child_pid is not None:
            try:
                os.waitpid(child_pid, 0)
            except ChildProcessError:
                pass
