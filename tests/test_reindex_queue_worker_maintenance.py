"""reindex worker 在维护 marker 下的待命与写入边界测试。"""

from __future__ import annotations

import argparse
from contextlib import contextmanager

import pytest


def _worker_args() -> argparse.Namespace:
    return argparse.Namespace(
        action="worker",
        idle_exit_sec=None,
        heartbeat_sec=None,
        require_execution_mode="isolated",
        owner_token=None,
    )


def test_worker在维护待命未交接时不读取配置或构造运行时() -> None:
    """marker 存在且不能证明待命身份时，worker 必须在任何写前退出。"""
    from codev_platform.ops.reindex_queue_worker import run_worker

    events: list[str] = []
    errors: list[str] = []

    result = run_worker(
        _worker_args(),
        out=lambda _message: None,
        err=errors.append,
        unused_common_args=lambda *_args, **_kwargs: [],
        worker_execution_mode=lambda _cfg: "isolated",
        build_isolated_worker=lambda *_args: (_ for _ in ()).throw(
            AssertionError("待命拒绝时不得构造隔离运行时")
        ),
        maintenance_waiter=lambda: events.append("wait") or False,
        config_loader=lambda: events.append("config") or {},
    )

    assert result == 1
    assert events == ["wait"]
    assert errors == ["FATAL: reindex 维护窗口已启用且待命身份无法证明；worker 不可运行"]


def test_worker待命交接完成后才读取配置() -> None:
    """恢复等待完成后才允许进入正常 worker 初始化路径。"""
    from codev_platform.ops.reindex_queue_worker import run_worker

    events: list[str] = []
    errors: list[str] = []

    result = run_worker(
        _worker_args(),
        out=lambda _message: None,
        err=errors.append,
        unused_common_args=lambda *_args, **_kwargs: [],
        worker_execution_mode=lambda _cfg: (_ for _ in ()).throw(ValueError("测试模式拒绝")),
        build_isolated_worker=lambda *_args: (_ for _ in ()).throw(
            AssertionError("配置模式拒绝前不得构造运行时")
        ),
        maintenance_waiter=lambda: events.append("wait") or True,
        config_loader=lambda: events.append("config") or {},
    )

    assert result == 1
    assert events == ["wait", "config"]
    assert errors == ["FATAL: 测试模式拒绝"]


def test_worker初始化许可拒绝时不读取配置不取得运行锁() -> None:
    """待命交接与 runtime 构造之间也必须复检 marker，不能留下 TOCTOU 写窗。"""
    from codev_platform.ops.reindex_queue_worker import run_worker

    events: list[str] = []
    errors: list[str] = []

    @contextmanager
    def _initialization_permit():
        events.append("permit-enter")
        yield False
        events.append("permit-exit")

    result = run_worker(
        _worker_args(),
        out=lambda _message: None,
        err=errors.append,
        unused_common_args=lambda *_args, **_kwargs: [],
        worker_execution_mode=lambda _cfg: pytest.fail("许可拒绝时不得解析执行模式"),
        build_isolated_worker=lambda *_args: pytest.fail("许可拒绝时不得构造运行时"),
        maintenance_waiter=lambda: events.append("wait") or True,
        maintenance_initialization_permit=_initialization_permit,
        config_loader=lambda: events.append("config") or {},
    )

    assert result == 1
    assert events == ["wait", "permit-enter", "permit-exit"]
    assert errors == ["FATAL: reindex 维护窗口已启用；worker 初始化被拒绝"]


def test_worker拒绝legacy常驻模式以避免维护窗口后续写入() -> None:
    """legacy ReindexWorker 没有逐轮 permit，不能再作为常驻生产路径。"""
    from codev_platform.ops.reindex_queue_worker import run_worker

    errors: list[str] = []

    @contextmanager
    def _initialization_permit():
        yield True

    result = run_worker(
        _worker_args(),
        out=lambda _message: None,
        err=errors.append,
        unused_common_args=lambda *_args, **_kwargs: [],
        worker_execution_mode=lambda _cfg: "legacy",
        build_isolated_worker=lambda *_args: pytest.fail("legacy 不得构造隔离运行时"),
        maintenance_waiter=lambda: True,
        maintenance_initialization_permit=_initialization_permit,
        config_loader=lambda: {},
    )

    assert result == 1
    assert errors == ["FATAL: 常驻 reindex worker 只允许 execution_mode=isolated"]


def test_worker运行锁不可证明时输出明确故障而非伪装已有进程(monkeypatch) -> None:
    """guard 忙或运行锁 I/O 故障必须保留给 systemd 可观测的失败原因。"""
    from codev_platform.ops.reindex_queue_worker import run_worker
    from codev_platform.reindex import supervisor

    @contextmanager
    def _unavailable_lock(_owner_token: str):
        raise supervisor.RunLockUnavailableError("运行锁原子协议不可用")
        yield False

    monkeypatch.setattr(supervisor, "acquire_run_lock", _unavailable_lock)
    errors: list[str] = []

    @contextmanager
    def _initialization_permit():
        yield True

    result = run_worker(
        _worker_args(),
        out=lambda _message: None,
        err=errors.append,
        unused_common_args=lambda *_args, **_kwargs: [],
        worker_execution_mode=lambda _cfg: "isolated",
        build_isolated_worker=lambda *_args: pytest.fail("运行锁不可证明时不得构造运行时"),
        maintenance_waiter=lambda: True,
        maintenance_initialization_permit=_initialization_permit,
        config_loader=lambda: {},
    )

    assert result == 1
    assert errors == ["FATAL: reindex 运行锁无法安全取得: 运行锁原子协议不可用"]


def test_worker初始化构造失败也记录退出并释放运行锁(tmp_path, monkeypatch) -> None:
    """短许可内失败不能留下看似仍运行的 supervisor 状态或僵尸 run lock。"""
    from codev_platform.ops.reindex_queue_worker import run_worker
    from codev_platform.reindex import supervisor

    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))

    @contextmanager
    def _initialization_permit():
        yield True

    with pytest.raises(RuntimeError, match="构造失败"):
        run_worker(
            _worker_args(),
            out=lambda _message: None,
            err=lambda _message: None,
            unused_common_args=lambda *_args, **_kwargs: [],
            worker_execution_mode=lambda _cfg: "isolated",
            build_isolated_worker=lambda *_args: (_ for _ in ()).throw(RuntimeError("构造失败")),
            maintenance_waiter=lambda: True,
            maintenance_initialization_permit=_initialization_permit,
            config_loader=lambda: {},
        )

    assert supervisor.worker_status()["exit_reason"] == "error"
    with supervisor.acquire_run_lock(supervisor.new_owner_token()) as acquired:
        assert acquired is True
