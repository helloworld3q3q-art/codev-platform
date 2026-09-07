"""root-fd worker 生命周期回归的隔离源码、控制进程与清理辅助。"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import codev_platform.runtime_bound_worker as worker
from codev_platform.core.process_tree import kill_process_tree, popen_tree


_PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _RuntimePaths:
    def __init__(self, root: Path) -> None:
        self.ready = root / "worker-ready"
        self.survivor = root / "descendant-survived"
        self.descendant_info = root / "descendant.info"
        self.worker_pid = root / "worker.pid"
        self.worker_info = root / "worker.info"
        self.fork_child_pid = root / "fork-child.pid"
        self.ack_unit = root / "ack-unit"
        self.ack_observed = root / "watchdog-ack"


def _runtime_paths(tmp_path: Path) -> tuple[Path, _RuntimePaths]:
    root = tmp_path / "runtime"
    root.mkdir()
    return root, _RuntimePaths(root)


def _prepare_lifecycle_source(tmp_path: Path) -> Path:
    source_root = tmp_path / "trusted-source"
    shutil.copytree(
        _PROJECT_ROOT / "codev_platform",
        source_root / "codev_platform",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return source_root


def _configure_worker_source(
    monkeypatch: pytest.MonkeyPatch,
    source_root: Path,
) -> None:
    monkeypatch.setattr(
        worker,
        "_BOOTSTRAP_PATH",
        source_root / "codev_platform" / "runtime_bound_worker_bootstrap.py",
    )
    monkeypatch.setattr(worker, "_SOURCE_ROOT", source_root)


def _patch_lingering_identity(source_root: Path, paths: _RuntimePaths) -> None:
    descendant = (
        "import os, pathlib, time; "
        "os.setsid(); "
        f"pathlib.Path({str(paths.descendant_info)!r}).write_text("
        "f'pid={os.getpid()} ppid={os.getppid()} pgrp={os.getpgrp()} sid={os.getsid(0)} '"
        'f\'cgroup={pathlib.Path("/proc/self/cgroup").read_text(encoding="ascii").strip()}\', '
        "encoding='ascii'); "
        "time.sleep(3.0); "
        f"pathlib.Path({str(paths.survivor)!r}).write_text('unexpected', encoding='utf-8')"
    )
    replacement = f"""
def _lifecycle_test_identity(payload: dict[str, object], root_descriptor: int) -> dict[str, object]:
    if payload:
        raise RuntimeBoundWorkerError("生命周期测试操作不接受请求字段")
    subprocess.Popen((sys.executable, "-B", "-c", {descendant!r}))
    Path({str(paths.worker_pid)!r}).write_text(str(os.getpid()), encoding="ascii")
    Path({str(paths.worker_info)!r}).write_text(
        f"pid={{os.getpid()}} ppid={{os.getppid()}} pgrp={{os.getpgrp()}} sid={{os.getsid(0)}} "
        "worker-spawned-descendant",
        encoding="ascii",
    )
    Path({str(paths.ready)!r}).write_text("ready", encoding="ascii")
    time.sleep(30)
    metadata = os.fstat(root_descriptor)
    return {{"device": int(metadata.st_dev), "inode": int(metadata.st_ino)}}


_OPERATIONS["root-identity"] = _lifecycle_test_identity
"""
    _inject_worker_source(source_root, replacement)


def _patch_immediate_descendant_identity(source_root: Path, paths: _RuntimePaths) -> None:
    descendant = (
        "import os, pathlib, signal, time; "
        "os.setsid(); "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(0.8); "
        f"pathlib.Path({str(paths.survivor)!r}).write_text('unexpected', encoding='utf-8')"
    )
    replacement = f"""
def _ack_lifecycle_identity(payload: dict[str, object], root_descriptor: int) -> dict[str, object]:
    if payload:
        raise RuntimeBoundWorkerError("ACK 生命周期测试操作不接受请求字段")
    subprocess.Popen((sys.executable, "-B", "-c", {descendant!r}))
    Path({str(paths.worker_pid)!r}).write_text(str(os.getpid()), encoding="ascii")
    Path({str(paths.ready)!r}).write_text("ready", encoding="ascii")
    metadata = os.fstat(root_descriptor)
    return {{"device": int(metadata.st_dev), "inode": int(metadata.st_ino)}}


_OPERATIONS["root-identity"] = _ack_lifecycle_identity
"""
    _inject_worker_source(source_root, replacement)


def _patch_completion_writer_retainer_identity(source_root: Path, paths: _RuntimePaths) -> None:
    replacement = f"""
def _completion_retainer_identity(payload: dict[str, object], root_descriptor: int) -> dict[str, object]:
    if payload:
        raise RuntimeBoundWorkerError("completion 生命周期测试操作不接受请求字段")
    child_process = os.fork()
    if child_process == 0:
        os.setsid()
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        __import__("time").sleep(0.8)
        Path({str(paths.survivor)!r}).write_text("unexpected", encoding="utf-8")
        os._exit(0)
    Path({str(paths.worker_pid)!r}).write_text(str(os.getpid()), encoding="ascii")
    Path({str(paths.ready)!r}).write_text("ready", encoding="ascii")
    raise RuntimeBoundWorkerError("模拟 bootstrap 失败")


_OPERATIONS["root-identity"] = _completion_retainer_identity
"""
    _inject_worker_source(source_root, replacement)


def _patch_descriptor_probe_identity(source_root: Path) -> None:
    replacement = """
def _descriptor_probe_identity(payload: dict[str, object], root_descriptor: int) -> dict[str, object]:
    if set(payload) != {"probe_fd"} or type(payload["probe_fd"]) is not int:
        raise RuntimeBoundWorkerError("描述符探针请求无效")
    try:
        os.fstat(payload["probe_fd"])
        probe_open = True
    except OSError:
        probe_open = False
    pidfd_visible = False
    for entry in Path("/proc/self/fd").iterdir():
        try:
            if os.readlink(entry) == "anon_inode:[pidfd]":
                pidfd_visible = True
                break
        except OSError:
            continue
    metadata = os.fstat(root_descriptor)
    return {
        "device": int(metadata.st_dev),
        "inode": int(metadata.st_ino),
        "probe_open": probe_open,
        "pidfd_visible": pidfd_visible,
        "root_fd_inheritable": os.get_inheritable(root_descriptor),
    }


_OPERATIONS["root-identity"] = _descriptor_probe_identity
"""
    _inject_worker_source(source_root, replacement)


def _patch_scope_identity(source_root: Path) -> None:
    replacement = """
def _scope_identity(payload: dict[str, object], root_descriptor: int) -> dict[str, object]:
    if payload:
        raise RuntimeBoundWorkerError("scope 身份测试操作不接受请求字段")
    metadata = os.fstat(root_descriptor)
    return {
        "device": int(metadata.st_dev),
        "inode": int(metadata.st_ino),
        "cgroup": Path("/proc/self/cgroup").read_text(encoding="ascii"),
    }


_OPERATIONS["root-identity"] = _scope_identity
"""
    _inject_worker_source(source_root, replacement)


def _delay_watchdog_start(source_root: Path, marker: Path) -> None:
    bootstrap_file = source_root / "codev_platform" / "runtime_bound_worker_bootstrap.py"
    source = bootstrap_file.read_text(encoding="utf-8")
    target = (
        "        completion_writer = _start_watchdog(parent_pidfd, bootstrap_pidfd, expected_unit)"
    )
    replacement = "\n".join(
        (
            f'        Path({str(marker)!r}).write_text(expected_unit, encoding="ascii")',
            '        __import__("time").sleep(0.5)',
            target,
        )
    )
    if source.count(target) != 1:
        pytest.fail("生命周期测试无法定位 watchdog 启动点")
    bootstrap_file.write_text(source.replace(target, replacement), encoding="utf-8")


def _delay_after_normal_ack(source_root: Path, marker: Path) -> None:
    bootstrap_file = source_root / "codev_platform" / "runtime_bound_worker_bootstrap.py"
    source = bootstrap_file.read_text(encoding="utf-8")
    target = "\n".join(
        (
            "        _send_normal_completion(completion_writer)",
            "        completion_writer = None",
            "        return 0",
        )
    )
    replacement = "\n".join(
        (
            "        _send_normal_completion(completion_writer)",
            f'        Path({str(marker)!r}).write_text(expected_unit, encoding="ascii")',
            '        __import__("time").sleep(0.5)',
            "        completion_writer = None",
            "        return 0",
        )
    )
    if source.count(target) != 1:
        pytest.fail("生命周期测试无法定位正常 ACK 返回点")
    bootstrap_file.write_text(source.replace(target, replacement), encoding="utf-8")


def _record_watchdog_ack(source_root: Path, marker: Path) -> None:
    watchdog_file = source_root / "codev_platform" / "runtime_bound_worker_watchdog.py"
    source = watchdog_file.read_text(encoding="utf-8")
    target = "\n".join(
        (
            "        if _wait_for_normal_completion(parent_pidfd, bootstrap_pidfd, completion_descriptor):",
            "            return 0",
        )
    )
    replacement = "\n".join(
        (
            "        if _wait_for_normal_completion(parent_pidfd, bootstrap_pidfd, completion_descriptor):",
            f'            Path({str(marker)!r}).write_text("ack", encoding="ascii")',
            "            return 0",
        )
    )
    if source.count(target) != 1:
        pytest.fail("生命周期测试无法定位 watchdog ACK 分支")
    watchdog_file.write_text(source.replace(target, replacement), encoding="utf-8")


def _inject_worker_source(source_root: Path, replacement: str) -> None:
    worker_file = source_root / "codev_platform" / "runtime_bound_worker.py"
    source = worker_file.read_text(encoding="utf-8")
    marker = "\n__all__ ="
    if source.count(marker) != 1:
        pytest.fail("生命周期测试无法定位 worker 主入口")
    worker_file.write_text(source.replace(marker, replacement + marker), encoding="utf-8")


def _assert_parent_death_reaps_descendant(
    tmp_path: Path,
    *,
    block_lifecycle_signals: bool = False,
    retain_fork_child: bool = False,
) -> None:
    root, paths = _runtime_paths(tmp_path)
    source_root = _prepare_lifecycle_source(tmp_path)
    _patch_lingering_identity(source_root, paths)
    controller = popen_tree(
        (
            sys.executable,
            "-B",
            "-c",
            _controller_script(
                source_root,
                root,
                block_lifecycle_signals,
                fork_child_marker=paths.fork_child_pid if retain_fork_child else None,
            ),
        ),
        cwd=_PROJECT_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    worker_pid: int | None = None
    retained_child_pid: int | None = None
    try:
        _wait_for_ready(controller, paths.ready)
        worker_pid = _read_pid(paths.worker_pid)
        retained_child_pid = _read_pid(paths.fork_child_pid)
        if retain_fork_child and retained_child_pid is None:
            pytest.fail("root-fd 生命周期测试未创建父 fork 保留子进程")
        if paths.survivor.exists():
            pytest.fail("root-fd 生命周期测试在杀死父进程前已出现孙进程写入")
        os.kill(controller.pid, signal.SIGKILL)
        controller.wait(timeout=5)
        if retain_fork_child and not _process_is_alive(retained_child_pid):
            pytest.fail("root-fd 生命周期测试中的父 fork 子进程意外退出")
        time.sleep(3.3)
        if paths.survivor.exists():
            pytest.fail(_survivor_diagnostic(paths, worker_pid))
    finally:
        if controller.poll() is None:
            kill_process_tree(controller)
            controller.wait(timeout=5)
        _stop_process(retained_child_pid)
        _cleanup_worker_scope(worker_pid or _read_pid(paths.worker_pid))


def _controller_script(
    source_root: Path,
    root: Path,
    block_lifecycle_signals: bool,
    *,
    fork_child_marker: Path | None = None,
    catch_worker_error: bool = False,
) -> str:
    lines = [
        "from pathlib import Path",
        "import os, signal",
    ]
    if block_lifecycle_signals:
        lines.append("signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGUSR1})")
    lines.extend(
        (
            "import codev_platform.runtime_bound_worker as worker",
            "from codev_platform.runtime_root_binding import RuntimeRootBinding",
            f"worker._BOOTSTRAP_PATH = Path({str(source_root / 'codev_platform' / 'runtime_bound_worker_bootstrap.py')!r})",
            f"worker._SOURCE_ROOT = Path({str(source_root)!r})",
            f"root = Path({str(root)!r})",
        )
    )
    if fork_child_marker is not None:
        lines.extend(
            (
                f"fork_child_marker = Path({str(fork_child_marker)!r})",
                "if hasattr(worker, '_open_parent_pidfd'):",
                "    original_parent_identity = worker._open_parent_pidfd",
                "    def open_parent_identity():",
                "        descriptor = original_parent_identity()",
                "        child_process = os.fork()",
                "        if child_process == 0:",
                "            fork_child_marker.write_text(str(os.getpid()), encoding='ascii')",
                "            __import__('time').sleep(10)",
                "            os._exit(0)",
                "        return descriptor",
                "    worker._open_parent_pidfd = open_parent_identity",
            )
        )
    if catch_worker_error:
        lines.extend(
            (
                "try:",
                "    with RuntimeRootBinding(root, os.geteuid()).bind() as bound_root:",
                "        worker.run_bound_operation(bound_root, 'root-identity', {}, timeout_sec=120.0)",
                "except worker.RuntimeBoundWorkerError:",
                "    __import__('time').sleep(2)",
            )
        )
    else:
        lines.extend(
            (
                "with RuntimeRootBinding(root, os.geteuid()).bind() as bound_root:",
                "    worker.run_bound_operation(bound_root, 'root-identity', {}, timeout_sec=120.0)",
            )
        )
    return "\n".join(lines)


def _wait_for_ready(controller: subprocess.Popen[str], ready: Path) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if ready.exists():
            return
        if controller.poll() is not None:
            stderr = controller.stderr.read() if controller.stderr is not None else ""
            pytest.fail(f"root-fd 生命周期控制进程提前退出：{stderr!r}")
        time.sleep(0.01)
    pytest.fail("root-fd 生命周期 worker 未在限定时间内就绪")


def _open_inheritable_probe_descriptor() -> int:
    read_descriptor, write_descriptor = os.pipe()
    probe_descriptor = 200
    try:
        if read_descriptor != probe_descriptor:
            os.dup2(read_descriptor, probe_descriptor, inheritable=True)
        else:
            os.set_inheritable(probe_descriptor, True)
    finally:
        if read_descriptor != probe_descriptor:
            _close_quietly(read_descriptor)
        _close_quietly(write_descriptor)
    return probe_descriptor


def _read_pid(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="ascii")
    except OSError:
        return None
    return int(value) if value.isdecimal() else None


def _process_is_alive(process_id: int | None) -> bool:
    if process_id is None:
        return False
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    return True


def _stop_process(process_id: int | None) -> None:
    if not _process_is_alive(process_id):
        return
    try:
        os.kill(process_id, signal.SIGKILL)
    except ProcessLookupError:
        return


def _cleanup_worker_scope(pid: int | None) -> None:
    if pid is None:
        return
    try:
        raw = Path(f"/proc/{pid}/cgroup").read_text(encoding="ascii")
    except OSError:
        return
    unit = next(
        (
            segment
            for line in raw.splitlines()
            for segment in line.split(":", 2)[-1].split("/")
            if segment.startswith("codev-rootfd-") and segment.endswith(".service")
        ),
        None,
    )
    _cleanup_scope_by_name(unit)


def _cleanup_scope_by_name(unit: str | None) -> None:
    if unit is None:
        return
    subprocess.run(
        ("/usr/bin/systemctl", "--user", "kill", "--kill-whom=all", "--signal=SIGKILL", unit),
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=3,
    )


def _scope_unit_from_cgroup(raw: str) -> str | None:
    for line in raw.splitlines():
        fields = line.split(":", 2)
        if len(fields) != 3:
            continue
        for segment in fields[2].split("/"):
            if segment.startswith("codev-rootfd-") and segment.endswith(".service"):
                return segment
    return None


def _scope_is_active(unit: str) -> bool:
    completed = subprocess.run(
        ("/usr/bin/systemctl", "--user", "show", unit, "--property=ActiveState", "--value"),
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=3,
    )
    return completed.returncode == 0 and completed.stdout.strip() == b"active"


def _wait_for_file(process: subprocess.Popen[str], path: Path, label: str) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr is not None else ""
            pytest.fail(f"root-fd 生命周期控制进程提前退出：{stderr!r}")
        time.sleep(0.01)
    pytest.fail(f"root-fd 生命周期未等到{label}")


def _read_text(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="ascii")
    except OSError:
        return None
    return value if value else None


def _survivor_diagnostic(paths: _RuntimePaths, pid: int | None) -> str:
    try:
        info = paths.worker_info.read_text(encoding="ascii")
    except OSError:
        info = "worker-info 缺失"
    try:
        descendant = paths.descendant_info.read_text(encoding="ascii")
    except OSError:
        descendant = "descendant-info 缺失"
    try:
        group = "未知" if pid is None else str(os.getpgid(pid))
    except OSError:
        group = "已退出"
    return (
        "root-fd worker 孙进程在父死亡后仍写入："
        f"worker={info}; descendant={descendant}; 当前进程组={group}"
    )


def _close_quietly(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass
