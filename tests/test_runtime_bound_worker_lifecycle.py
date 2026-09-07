"""root-fd worker 的真实 user cgroup 生命周期回归。"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from codev_platform.core.process_tree import kill_process_tree, popen_tree
from codev_platform.runtime_bound_worker import RuntimeBoundWorkerError, run_bound_operation
from codev_platform.runtime_root_binding import RuntimeRootBinding
from tests.runtime_bound_worker_lifecycle_support import (
    _assert_parent_death_reaps_descendant,
    _cleanup_scope_by_name,
    _cleanup_worker_scope,
    _close_quietly,
    _configure_worker_source,
    _controller_script,
    _delay_after_normal_ack,
    _delay_watchdog_start,
    _open_inheritable_probe_descriptor,
    _patch_completion_writer_retainer_identity,
    _patch_descriptor_probe_identity,
    _patch_immediate_descendant_identity,
    _patch_lingering_identity,
    _patch_scope_identity,
    _prepare_lifecycle_source,
    _read_pid,
    _read_text,
    _record_watchdog_ack,
    _runtime_paths,
    _scope_is_active,
    _scope_unit_from_cgroup,
    _wait_for_file,
    _wait_for_ready,
)


_LINUX = sys.platform.startswith("linux")
_PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not _LINUX, reason="需要 Linux root-fd worker 生命周期")
def testrootfdworker超时会回收真实启动链派生的孙进程(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """timeout 必须清空 transient cgroup，即使孙进程主动脱离进程组。"""
    root, paths = _runtime_paths(tmp_path)
    source_root = _prepare_lifecycle_source(tmp_path)
    _patch_lingering_identity(source_root, paths)
    _configure_worker_source(monkeypatch, source_root)

    with RuntimeRootBinding(root, os.geteuid()).bind() as bound_root:
        with pytest.raises(RuntimeBoundWorkerError):
            run_bound_operation(bound_root, "root-identity", {}, timeout_sec=0.5)

    assert paths.ready.exists()
    time.sleep(1.1)
    assert not paths.survivor.exists()


@pytest.mark.skipif(not _LINUX, reason="需要 Linux root-fd worker 生命周期")
def testrootfdworker父进程死亡会回收setsid孙进程(tmp_path: Path) -> None:
    """父锁宿主被杀死后，setsid() 脱组孙进程仍不得在旧根写入。"""
    _assert_parent_death_reaps_descendant(tmp_path)


@pytest.mark.skipif(not _LINUX, reason="需要 Linux pthread_sigmask")
def testrootfdworker解除继承的生命周期信号屏蔽后父死亡仍回收孙进程(
    tmp_path: Path,
) -> None:
    """watchdog 与实际 worker 都须解除继承终止信号屏蔽。"""
    _assert_parent_death_reaps_descendant(tmp_path, block_lifecycle_signals=True)


@pytest.mark.skipif(not _LINUX, reason="需要 Linux pidfd 父死亡语义")
def testrootfdworker父fork出长期子进程后死亡仍回收setsid孙进程(
    tmp_path: Path,
) -> None:
    """fork 子进程不得影响原父 pidfd 的精确死亡判定。"""
    _assert_parent_death_reaps_descendant(tmp_path, retain_fork_child=True)


@pytest.mark.skipif(not _LINUX, reason="需要 Linux root-fd worker 生命周期")
def testrootfdworker父死在watchdog就绪前不会启动operation(tmp_path: Path) -> None:
    """父端在 bootstrap 尚未放行 operation 时死亡，scope 必须先收口再拒绝启动。"""
    root, paths = _runtime_paths(tmp_path)
    source_root = _prepare_lifecycle_source(tmp_path)
    _patch_lingering_identity(source_root, paths)
    before_watchdog = root / "before-watchdog"
    _delay_watchdog_start(source_root, before_watchdog)
    controller = popen_tree(
        (sys.executable, "-B", "-c", _controller_script(source_root, root, False)),
        cwd=_PROJECT_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_file(controller, before_watchdog, "watchdog 前置标记")
        os.kill(controller.pid, signal.SIGKILL)
        controller.wait(timeout=5)
        time.sleep(1.0)

        assert not paths.ready.exists()
        assert not paths.survivor.exists()
    finally:
        if controller.poll() is None:
            kill_process_tree(controller)
            controller.wait(timeout=5)
        _cleanup_scope_by_name(_read_text(before_watchdog))


@pytest.mark.skipif(not _LINUX, reason="需要 Linux root-fd worker 生命周期")
def testrootfdworker关闭无关描述符并使根描述符不可继承(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """child 只能继承 root fd，且进入 operation 前必须将其改为不可继承。"""
    root, _paths = _runtime_paths(tmp_path)
    source_root = _prepare_lifecycle_source(tmp_path)
    _patch_descriptor_probe_identity(source_root)
    _configure_worker_source(monkeypatch, source_root)
    probe_fd = _open_inheritable_probe_descriptor()
    try:
        with RuntimeRootBinding(root, os.geteuid()).bind() as bound_root:
            result = run_bound_operation(bound_root, "root-identity", {"probe_fd": probe_fd})
    finally:
        _close_quietly(probe_fd)

    assert result["probe_open"] is False
    assert result["pidfd_visible"] is False
    assert result["root_fd_inheritable"] is False


@pytest.mark.skipif(not _LINUX, reason="需要 Linux 隔离启动门")
def testrootfdworker隔离启动门不在受信源码镜像写入pycache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """-B 必须在真实隔离解释器中生效，不能依赖被 -I 忽略的环境变量。"""
    root, _paths = _runtime_paths(tmp_path)
    source_root = _prepare_lifecycle_source(tmp_path)
    _configure_worker_source(monkeypatch, source_root)

    with RuntimeRootBinding(root, os.geteuid()).bind() as bound_root:
        run_bound_operation(bound_root, "root-identity", {})

    assert not tuple((source_root / "codev_platform").rglob("__pycache__"))


@pytest.mark.skipif(not _LINUX, reason="需要 Linux user systemd cgroup")
def testrootfdworker正常完成会撤防并收口瞬时cgroup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成功响应返回前 watchdog 必须已收到 ACK 并退出，不得留下延迟清理者。"""
    root, _paths = _runtime_paths(tmp_path)
    source_root = _prepare_lifecycle_source(tmp_path)
    _patch_scope_identity(source_root)
    _configure_worker_source(monkeypatch, source_root)

    with RuntimeRootBinding(root, os.geteuid()).bind() as bound_root:
        result = run_bound_operation(bound_root, "root-identity", {})

    unit = _scope_unit_from_cgroup(str(result["cgroup"]))
    assert unit is not None
    assert not _scope_is_active(unit)


@pytest.mark.skipif(not _LINUX, reason="需要 Linux user systemd cgroup")
def testrootfdworker正常ACK后父死仍收口setsid后代(tmp_path: Path) -> None:
    """watchdog 已确认 ACK 后，主 bootstrap 退出仍必须由 manager 清空后代。"""
    root, paths = _runtime_paths(tmp_path)
    source_root = _prepare_lifecycle_source(tmp_path)
    _patch_immediate_descendant_identity(source_root, paths)
    _delay_after_normal_ack(source_root, paths.ack_unit)
    _record_watchdog_ack(source_root, paths.ack_observed)
    controller = popen_tree(
        (sys.executable, "-B", "-c", _controller_script(source_root, root, False)),
        cwd=_PROJECT_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_ready(controller, paths.ready)
        _wait_for_file(controller, paths.ack_observed, "watchdog ACK 确认")
        unit = _read_text(paths.ack_unit)
        assert unit is not None
        os.kill(controller.pid, signal.SIGKILL)
        controller.wait(timeout=5)
        time.sleep(1.2)

        assert not paths.survivor.exists()
    finally:
        if controller.poll() is None:
            kill_process_tree(controller)
            controller.wait(timeout=5)
        _cleanup_scope_by_name(_read_text(paths.ack_unit))


@pytest.mark.skipif(not _LINUX, reason="需要 Linux bootstrap pidfd 生命周期")
def testrootfdworkerbootstrap异常且fork保留完成描述符仍回收后代(
    tmp_path: Path,
) -> None:
    """bootstrap 异常后，fork 子进程持有 completion 写端也不能让 watchdog 永久等待。"""
    root, paths = _runtime_paths(tmp_path)
    source_root = _prepare_lifecycle_source(tmp_path)
    _patch_completion_writer_retainer_identity(source_root, paths)
    controller = popen_tree(
        (
            sys.executable,
            "-B",
            "-c",
            _controller_script(source_root, root, False, catch_worker_error=True),
        ),
        cwd=_PROJECT_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    worker_pid: int | None = None
    try:
        _wait_for_ready(controller, paths.ready)
        worker_pid = _read_pid(paths.worker_pid)
        time.sleep(1.2)

        assert not paths.survivor.exists()
    finally:
        if controller.poll() is None:
            kill_process_tree(controller)
            controller.wait(timeout=5)
        _cleanup_worker_scope(worker_pid or _read_pid(paths.worker_pid))
