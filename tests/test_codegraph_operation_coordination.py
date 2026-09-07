"""CodeGraph 写意图与多仓操作协调的回归测试。"""
from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest


def _等待条件成立(条件: Callable[[], bool], *, 超时秒: float = 5.0) -> bool:
    截止时间 = time.monotonic() + 超时秒
    while time.monotonic() < 截止时间:
        if 条件():
            return True
        time.sleep(0.01)
    return 条件()


def _启动持有写意图的进程(repo: Path, lock_root: Path, ready: Path) -> subprocess.Popen[str]:
    script = """
from pathlib import Path
import sys
import time
from codev_platform.codegraph.operation_lease import codegraph_reindex_leases

repo, lock_root, ready = map(Path, sys.argv[1:])
with codegraph_reindex_leases([repo], timeout_sec=1.0, lock_root=lock_root):
    ready.write_text("ready", encoding="utf-8")
    while True:
        time.sleep(1.0)
"""
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(repo),
            str(lock_root),
            str(ready),
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    if _等待条件成立(lambda: ready.exists() or process.poll() is not None) and ready.exists():
        return process
    if process.poll() is None:
        process.terminate()
    stdout, stderr = process.communicate(timeout=5)
    pytest.fail(f"写意图子进程未就绪: stdout={stdout!r}, stderr={stderr!r}")


def test_空闲单仓在零超时下仍允许每把锁首次尝试(tmp_path: Path) -> None:
    from codev_platform.codegraph.operation_lease import codegraph_reindex_leases

    repo = tmp_path / "repo"
    repo.mkdir()

    with codegraph_reindex_leases(
        [repo],
        timeout_sec=0.0,
        lock_root=tmp_path / "locks",
    ):
        pass


def test_空闲多仓在零超时下仍允许每把锁首次尝试(tmp_path: Path) -> None:
    from codev_platform.codegraph.operation_lease import codegraph_reindex_leases

    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    repo_a.mkdir()
    repo_b.mkdir()

    with codegraph_reindex_leases(
        [repo_b, repo_a],
        timeout_sec=0.0,
        lock_root=tmp_path / "locks",
    ):
        pass


def test_空闲仓在极小预算下仍实际尝试并取得锁(tmp_path: Path) -> None:
    from codev_platform.codegraph.operation_lease import codegraph_reindex_leases

    repo = tmp_path / "repo"
    repo.mkdir()
    已进入: list[bool] = []

    with codegraph_reindex_leases(
        [repo],
        timeout_sec=1e-12,
        lock_root=tmp_path / "locks",
    ):
        已进入.append(True)

    assert 已进入 == [True]


def test_写意图跨进程可见且按仓隔离并在进程退出后自动释放(tmp_path: Path) -> None:
    from codev_platform.codegraph.operation_lease import CodegraphOperationLeaseBusyError
    from codev_platform.codegraph.operation_lease import codegraph_reindex_leases
    from codev_platform.codegraph.operation_lease import codegraph_writer_pending

    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    repo_a.mkdir()
    repo_b.mkdir()
    lock_root = tmp_path / "locks"
    ready = tmp_path / "ready"
    process = _启动持有写意图的进程(repo_b, lock_root, ready)

    try:
        assert codegraph_writer_pending(repo_b, lock_root=lock_root) is True
        assert codegraph_writer_pending(repo_a, lock_root=lock_root) is False

        with pytest.raises(CodegraphOperationLeaseBusyError, match="超时|正忙"):
            with codegraph_reindex_leases(
                [repo_a, repo_b],
                timeout_sec=0.05,
                lock_root=lock_root,
            ):
                pytest.fail("写意图未全部取得时不得进入重建临界区")

        assert codegraph_writer_pending(repo_a, lock_root=lock_root) is False
        assert codegraph_writer_pending(repo_b, lock_root=lock_root) is True
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=5)

    assert _等待条件成立(
        lambda: not codegraph_writer_pending(repo_b, lock_root=lock_root)
    )


def test_多仓先取得全部写意图再按稳定顺序取得操作租约并逆序释放(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.codegraph import operation_lease

    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    repo_a.mkdir()
    repo_b.mkdir()
    lock_root = tmp_path / "locks"
    仓名 = {
        operation_lease.lease_path_for_repo(repo_a, lock_root=lock_root).name: "甲",
        operation_lease.lease_path_for_repo(repo_b, lock_root=lock_root).name: "乙",
    }
    事件: list[tuple[str, str]] = []
    剩余预算: list[float] = []
    当前时间 = {"值": 10.0}
    monkeypatch.setattr(operation_lease.time, "monotonic", lambda: 当前时间["值"])

    @contextmanager
    def 假锁(path: Path, deadline, *, blocking: bool) -> Iterator[bool]:
        assert blocking is True
        deadline.check()
        剩余预算.append(deadline.remaining())
        if len(剩余预算) == 1:
            当前时间["值"] = 12.0
        阶段 = "操作租约" if path.parent == lock_root else "写意图"
        当前仓 = 仓名[path.name]
        事件.append((f"{阶段}进入", 当前仓))
        try:
            yield True
        finally:
            事件.append((f"{阶段}退出", 当前仓))

    monkeypatch.setattr(operation_lease, "advisory_lock", 假锁)

    with operation_lease.codegraph_reindex_leases(
        [repo_b, repo_a, repo_b],
        timeout_sec=1.0,
        lock_root=lock_root,
    ):
        assert 事件 == [
            ("写意图进入", "甲"),
            ("写意图进入", "乙"),
            ("操作租约进入", "甲"),
            ("操作租约进入", "乙"),
        ]

    assert 事件 == [
        ("写意图进入", "甲"),
        ("写意图进入", "乙"),
        ("操作租约进入", "甲"),
        ("操作租约进入", "乙"),
        ("操作租约退出", "乙"),
        ("操作租约退出", "甲"),
        ("写意图退出", "乙"),
        ("写意图退出", "甲"),
    ]
    assert 剩余预算 == [1.0, 0.0, 0.0, 0.0]


def test_零预算竞争在一次实际尝试后仍报告忙碌(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.codegraph import operation_lease

    repo = tmp_path / "repo"
    repo.mkdir()
    尝试路径: list[Path] = []

    @contextmanager
    def 假竞争锁(path: Path, deadline, *, blocking: bool) -> Iterator[bool]:
        assert blocking is True
        deadline.check()
        尝试路径.append(path)
        with pytest.raises(operation_lease.CodegraphOperationLeaseBusyError):
            deadline.check()
        yield False

    monkeypatch.setattr(operation_lease, "advisory_lock", 假竞争锁)

    with pytest.raises(operation_lease.CodegraphOperationLeaseBusyError):
        with operation_lease.codegraph_reindex_leases(
            [repo],
            timeout_sec=0.0,
            lock_root=tmp_path / "locks",
        ):
            pytest.fail("竞争锁不得进入重建临界区")

    assert len(尝试路径) == 1


@pytest.mark.parametrize("失败阶段", ["写意图", "操作租约"])
def test_多仓协调任一阶段失败时逆序释放已取得的锁(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    失败阶段: str,
) -> None:
    from codev_platform.codegraph import operation_lease

    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    repo_a.mkdir()
    repo_b.mkdir()
    lock_root = tmp_path / "locks"
    仓名 = {
        operation_lease.lease_path_for_repo(repo_a, lock_root=lock_root).name: "甲",
        operation_lease.lease_path_for_repo(repo_b, lock_root=lock_root).name: "乙",
    }
    事件: list[tuple[str, str]] = []

    @contextmanager
    def 假锁(path: Path, deadline, *, blocking: bool) -> Iterator[bool]:
        assert blocking is True
        deadline.check()
        阶段 = "操作租约" if path.parent == lock_root else "写意图"
        当前仓 = 仓名[path.name]
        事件.append((f"{阶段}进入", 当前仓))
        if 阶段 == 失败阶段 and 当前仓 == "乙":
            事件.append((f"{阶段}失败", 当前仓))
            raise operation_lease.CodegraphOperationLeaseBusyError("模拟超时")
        try:
            yield True
        finally:
            事件.append((f"{阶段}退出", 当前仓))

    monkeypatch.setattr(operation_lease, "advisory_lock", 假锁)

    with pytest.raises(operation_lease.CodegraphOperationLeaseBusyError):
        with operation_lease.codegraph_reindex_leases(
            [repo_a, repo_b],
            timeout_sec=1.0,
            lock_root=lock_root,
        ):
            pytest.fail("任一阶段失败时不得进入重建临界区")

    if 失败阶段 == "写意图":
        assert 事件 == [
            ("写意图进入", "甲"),
            ("写意图进入", "乙"),
            ("写意图失败", "乙"),
            ("写意图退出", "甲"),
        ]
    else:
        assert 事件 == [
            ("写意图进入", "甲"),
            ("写意图进入", "乙"),
            ("操作租约进入", "甲"),
            ("操作租约进入", "乙"),
            ("操作租约失败", "乙"),
            ("操作租约退出", "甲"),
            ("写意图退出", "乙"),
            ("写意图退出", "甲"),
        ]


def test_仓集合迭代异常被包装且不泄露原消息(tmp_path: Path) -> None:
    from codev_platform.codegraph import operation_lease

    class 损坏仓集合:
        def __iter__(self) -> Iterator[Path]:
            raise OSError("敏感迭代器故障")

    with pytest.raises(operation_lease.CodegraphOperationLeaseError) as raised:
        with operation_lease.codegraph_reindex_leases(
            损坏仓集合(),
            lock_root=tmp_path / "locks",
        ):
            pytest.fail("仓集合不可信时不得进入重建临界区")

    assert "敏感迭代器故障" not in str(raised.value)


def test_截止时间构造异常被包装且不泄露原消息(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.codegraph import operation_lease

    repo = tmp_path / "repo"
    repo.mkdir()

    def 损坏单调时钟() -> float:
        raise OSError("敏感时钟故障")

    monkeypatch.setattr(operation_lease.time, "monotonic", 损坏单调时钟)

    with pytest.raises(operation_lease.CodegraphOperationLeaseError) as raised:
        with operation_lease.codegraph_reindex_leases(
            [repo],
            lock_root=tmp_path / "locks",
        ):
            pytest.fail("截止时间不可构造时不得进入重建临界区")

    assert "敏感时钟故障" not in str(raised.value)


def test_重建协调临界区业务异常原样传播(tmp_path: Path) -> None:
    from codev_platform.codegraph.operation_lease import codegraph_reindex_leases

    repo = tmp_path / "repo"
    repo.mkdir()

    with pytest.raises(FileNotFoundError, match="业务子进程不存在"):
        with codegraph_reindex_leases(
            [repo],
            lock_root=tmp_path / "locks",
        ):
            raise FileNotFoundError("业务子进程不存在")
