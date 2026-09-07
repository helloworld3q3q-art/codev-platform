"""reindex worker 运行锁跨进程协议的并发与故障回归。"""
from __future__ import annotations

import json
import multiprocessing
import os
import threading
from contextlib import contextmanager

import pytest

from codev_platform.reindex import run_lock_protocol, supervisor


def _slow_run_lock_publisher(
    data_dir: str,
    write_started,
    release_write,
    reports,
) -> None:
    """子进程在创建后、写入身份记录前停住，用于验证真实 OS guard。"""
    os.environ["PLATFORM_DATA_DIR"] = data_dir
    from codev_platform.reindex import supervisor as child_supervisor

    original_write = child_supervisor.os.write

    def pause_payload_write(fd: int, payload: bytes) -> int:
        if payload.startswith(b"{"):
            write_started.set()
            if not release_write.wait(timeout=10):
                raise RuntimeError("测试未释放跨进程运行锁写入")
        return original_write(fd, payload)

    child_supervisor.os.write = pause_payload_write
    try:
        with child_supervisor.acquire_run_lock("slow-publisher") as acquired:
            reports.put(("slow", acquired, ""))
    except BaseException as error:  # 子进程必须把中断类型传回父进程断言。
        reports.put(("slow", False, type(error).__name__))
    finally:
        child_supervisor.os.write = original_write


def _run_lock_contender(data_dir: str, reports) -> None:
    """在独立进程中尝试取得同一运行锁。"""
    os.environ["PLATFORM_DATA_DIR"] = data_dir
    from codev_platform.reindex import supervisor as child_supervisor

    try:
        with child_supervisor.acquire_run_lock("second-process") as acquired:
            reports.put(("second", acquired, ""))
    except BaseException as error:  # guard 忙必须作为错误而非假成功返回。
        reports.put(("second", False, type(error).__name__))


def test_run_lock写入未完成时第二持有者必须失败关闭(tmp_path, monkeypatch):
    """创建锁文件到写完身份记录之间也必须由跨进程协议保护。"""
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    original_write = supervisor.os.write
    first_write_started = threading.Event()
    release_first_write = threading.Event()
    second_finished = threading.Event()
    results: dict[str, bool] = {}
    errors: dict[str, BaseException] = {}

    def pause_first_payload_write(fd: int, payload: bytes) -> int:
        if (
            threading.current_thread().name == "run-lock-first"
            and payload.startswith(b"{")
        ):
            first_write_started.set()
            if not release_first_write.wait(timeout=5):
                raise RuntimeError("测试未释放首个运行锁写入")
        return original_write(fd, payload)

    monkeypatch.setattr(supervisor.os, "write", pause_first_payload_write)

    def acquire_first() -> None:
        try:
            with supervisor.acquire_run_lock("first-owner") as acquired:
                results["first"] = acquired
        except BaseException as error:  # 测试必须保留进程级中断的真实类型。
            errors["first"] = error

    def acquire_second() -> None:
        try:
            with supervisor.acquire_run_lock("second-owner") as acquired:
                results["second"] = acquired
        except BaseException as error:  # 运行锁不可证明时必须有可观测失败。
            errors["second"] = error
        finally:
            second_finished.set()

    first = threading.Thread(target=acquire_first, name="run-lock-first")
    second = threading.Thread(target=acquire_second, name="run-lock-second")
    first.start()
    assert first_write_started.wait(timeout=5)
    second.start()
    assert second_finished.wait(timeout=5)
    release_first_write.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors.get("first") is None
    assert results["first"] is True
    assert results.get("second") is not True
    assert type(errors["second"]).__name__ == "RunLockUnavailableError"


def test_run_lock跨进程写入窗口内第二持有者失败关闭(tmp_path, monkeypatch):
    """真实进程而非线程语义下，guard 也必须保护创建到完整写入的窗口。"""
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    context = multiprocessing.get_context("spawn")
    write_started = context.Event()
    release_write = context.Event()
    reports = context.Queue()
    slow = context.Process(
        target=_slow_run_lock_publisher,
        args=(str(tmp_path), write_started, release_write, reports),
    )
    slow.start()
    assert write_started.wait(timeout=10)
    contender = context.Process(
        target=_run_lock_contender,
        args=(str(tmp_path), reports),
    )
    contender.start()
    label, acquired, error_name = reports.get(timeout=10)
    release_write.set()
    slow.join(timeout=10)
    contender.join(timeout=10)

    assert label == "second"
    assert acquired is False
    assert error_name == "RunLockUnavailableError"
    assert slow.exitcode == 0
    assert contender.exitcode == 0
    assert reports.get(timeout=10) == ("slow", True, "")


def test_run_lock写入中断时清理自身未完成文件并原样传播(tmp_path, monkeypatch):
    """写入中断后不能遗留会被下一进程误判的空运行锁。"""
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))

    def interrupt_write(_fd: int, _payload: bytes) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(supervisor.os, "write", interrupt_write)

    with pytest.raises(KeyboardInterrupt):
        with supervisor.acquire_run_lock("interrupted-owner"):
            pytest.fail("中断写入后不得进入 worker 临界区")

    assert not supervisor._run_lock_path().exists()


def test_run_lock回收旧版中断遗留的空文件(tmp_path, monkeypatch):
    """历史版本在写入前崩溃留下空文件时，新 worker 必须在 guard 内原子回收。"""
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    supervisor.runtime_dir()
    supervisor._run_lock_path().write_bytes(b"")

    with supervisor.acquire_run_lock("recovery-owner") as acquired:
        assert acquired is True
        assert supervisor._lock_state()["owner_token"] == "recovery-owner"


def test_run_lock并发回收同一陈旧记录时仅一个持有者成功(tmp_path, monkeypatch):
    """两个候选 worker 同时发现 stale lock 时，也只能有一个进入临界区。"""
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor, "is_pid_running", lambda _pid: False)
    monkeypatch.setattr(supervisor, "_process_birth_identity", lambda _pid: "test-birth")
    supervisor.runtime_dir()
    supervisor._run_lock_path().write_text(
        json.dumps({"pid": 999999, "owner_token": "stale-owner"}),
        encoding="utf-8",
    )
    start = threading.Event()
    release_winner = threading.Event()
    winner_ready = threading.Event()
    outcomes: list[bool] = []
    errors: list[BaseException] = []

    def contender(owner_token: str) -> None:
        assert start.wait(timeout=5)
        try:
            with supervisor.acquire_run_lock(owner_token) as acquired:
                outcomes.append(acquired)
                if acquired:
                    winner_ready.set()
                    assert release_winner.wait(timeout=5)
        except BaseException as error:  # guard 忙属于显式拒绝，不得伪装成成功。
            errors.append(error)

    first = threading.Thread(target=contender, args=("first-owner",))
    second = threading.Thread(target=contender, args=("second-owner",))
    first.start()
    second.start()
    start.set()
    assert winner_ready.wait(timeout=5)
    release_winner.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert outcomes.count(True) == 1
    assert outcomes.count(False) + len(errors) == 1
    assert all(type(error).__name__ == "RunLockUnavailableError" for error in errors)


def test_run_lock_guard忙时以可观测错误失败关闭(tmp_path, monkeypatch):
    """guard 暂时不可用不能退化为“已有 worker”或允许第二个 worker 启动。"""
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))

    @contextmanager
    def busy_guard(*_args, **_kwargs):
        yield False

    monkeypatch.setattr(run_lock_protocol, "advisory_lock", busy_guard)

    with pytest.raises(supervisor.RunLockUnavailableError, match="guard 正忙"):
        with supervisor.acquire_run_lock("guard-busy-owner"):
            pytest.fail("guard 忙时不得进入 worker 临界区")


def test_run_lock读取现存记录发生I_O异常时保留原锁并失败关闭(tmp_path, monkeypatch):
    """读活锁无法证明时不能按空锁或 stale 锁删除。"""
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    lock_path = supervisor._run_lock_path()
    original = b'{"lock_schema_version":1,"pid":999,"owner_token":"owner"}'
    supervisor.runtime_dir()
    lock_path.write_bytes(original)

    def deny_read(*_args, **_kwargs) -> bytes:
        raise PermissionError("模拟运行锁读取被拒绝")

    monkeypatch.setattr(
        run_lock_protocol,
        "read_regular_file_bounded",
        deny_read,
        raising=False,
    )

    with pytest.raises(supervisor.RunLockUnavailableError, match="读取无法证明"):
        with supervisor.acquire_run_lock("read-failure-owner"):
            pytest.fail("读取活锁失败时不得进入 worker 临界区")

    assert lock_path.read_bytes() == original


def test_运行锁严格读取异常时自动启动判断保守视为占用(tmp_path, monkeypatch):
    """状态展示可降级为空，但 launcher 不得据此生成候选 worker。"""
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    supervisor.runtime_dir()
    supervisor._run_lock_path().write_bytes(b'{"lock_schema_version":1}')

    def deny_read(*_args, **_kwargs) -> bytes:
        raise PermissionError("模拟运行锁读取被拒绝")

    monkeypatch.setattr(
        run_lock_protocol,
        "read_regular_file_bounded",
        deny_read,
        raising=False,
    )

    assert supervisor._lock_running() is True


def test_run_lock活跃身份探针异常时不回收而失败关闭(tmp_path, monkeypatch):
    """身份判定本身出错时不能把未知锁错判成 stale。"""
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    supervisor.runtime_dir()
    lock_path = supervisor._run_lock_path()
    lock_path.write_text(json.dumps({"pid": 123, "owner_token": "unknown"}), encoding="utf-8")

    def fail_probe(_lock: dict):
        raise RuntimeError("身份探针不可用")

    monkeypatch.setattr(supervisor, "_run_lock_disposition", fail_probe)

    with pytest.raises(supervisor.RunLockUnavailableError, match="活跃状态无法证明"):
        with supervisor.acquire_run_lock("probe-failure-owner"):
            pytest.fail("活跃身份无法证明时不得进入 worker 临界区")

    assert lock_path.exists()


def test_run_lock发布失败时转换为可观测错误并清理(tmp_path, monkeypatch):
    """普通 I/O 故障不能漏成原始异常或遗留半成品锁。"""
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))

    def fail_write(_fd: int, _payload: bytes) -> int:
        raise OSError("模拟磁盘写入失败")

    monkeypatch.setattr(supervisor.os, "write", fail_write)

    with pytest.raises(supervisor.RunLockUnavailableError, match="原子协议不可用"):
        with supervisor.acquire_run_lock("write-failure-owner"):
            pytest.fail("锁发布失败时不得进入 worker 临界区")

    assert not supervisor._run_lock_path().exists()


def test_heartbeat只更新状态文件而不重建运行锁(tmp_path, monkeypatch):
    """锁文件只承载唯一持有者身份，心跳写入不得绕过同一 guard。"""
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    owner_token = "heartbeat-owner"

    with supervisor.acquire_run_lock(owner_token) as acquired:
        assert acquired is True
        supervisor.record_worker_start(owner_token, mode="short")
        before = supervisor._run_lock_path().read_bytes()
        supervisor.record_heartbeat(owner_token)

        assert supervisor._run_lock_path().read_bytes() == before
