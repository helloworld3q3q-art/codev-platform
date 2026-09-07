"""CodeGraph 重建与 MCP 后端共享操作租约的回归测试。"""
from __future__ import annotations

import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest


def test_同一仓的租约路径对等价绝对路径稳定(tmp_path: Path) -> None:
    from codev_platform.codegraph.operation_lease import lease_path_for_repo

    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "locks"

    assert lease_path_for_repo(repo, lock_root=root) == lease_path_for_repo(
        repo.resolve(),
        lock_root=root,
    )


def test_租约拒绝相对仓路径且不创建锁目录(tmp_path: Path) -> None:
    from codev_platform.codegraph.operation_lease import CodegraphOperationLeaseError
    from codev_platform.codegraph.operation_lease import lease_path_for_repo

    root = tmp_path / "locks"

    with pytest.raises(CodegraphOperationLeaseError):
        lease_path_for_repo(Path("relative-repo"), lock_root=root)

    assert not root.exists()


def test_另一进程持有租约时当前进程失败关闭(tmp_path: Path) -> None:
    from codev_platform.codegraph.operation_lease import CodegraphOperationLeaseBusyError
    from codev_platform.codegraph.operation_lease import codegraph_operation_lease

    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "locks"
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    script = """
from pathlib import Path
import sys
import time
from codev_platform.codegraph.operation_lease import codegraph_operation_lease

repo, lock_root, ready, release = map(Path, sys.argv[1:])
with codegraph_operation_lease(repo, lock_root=lock_root):
    ready.write_text('ready', encoding='utf-8')
    while not release.exists():
        time.sleep(0.01)
"""
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(repo),
            str(root),
            str(ready),
            str(release),
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        deadline = time.monotonic() + 5.0
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), process.communicate(timeout=1)[1]

        with pytest.raises(CodegraphOperationLeaseBusyError):
            with codegraph_operation_lease(repo, lock_root=root):
                pytest.fail("另一进程持有租约时不得进入 CodeGraph 操作")
    finally:
        release.write_text("release", encoding="utf-8")
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, (stdout, stderr)


def test_租约释放后允许下一位操作(tmp_path: Path) -> None:
    from codev_platform.codegraph.operation_lease import codegraph_operation_lease

    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "locks"
    events: list[str] = []

    with codegraph_operation_lease(repo, lock_root=root):
        events.append("first")
    with codegraph_operation_lease(repo, lock_root=root):
        events.append("second")

    assert events == ["first", "second"]


def test_底层锁异常不泄露原始错误并失败关闭(tmp_path: Path, monkeypatch) -> None:
    from codev_platform.codegraph import operation_lease

    repo = tmp_path / "repo"
    repo.mkdir()

    def broken_lock(*_args, **_kwargs):
        raise OSError("敏感底层路径")

    monkeypatch.setattr(operation_lease, "advisory_lock", broken_lock)

    with pytest.raises(operation_lease.CodegraphOperationLeaseError) as raised:
        with operation_lease.codegraph_operation_lease(repo, lock_root=tmp_path / "locks"):
            pytest.fail("锁异常不得放行")

    assert "敏感底层路径" not in str(raised.value)


def test_租约内部业务异常必须原样交还调用方(tmp_path: Path) -> None:
    from codev_platform.codegraph.operation_lease import codegraph_operation_lease

    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "locks"

    with pytest.raises(FileNotFoundError, match="业务子进程不存在"):
        with codegraph_operation_lease(repo, lock_root=root):
            raise FileNotFoundError("业务子进程不存在")

    with codegraph_operation_lease(repo, lock_root=root):
        pass


def test_多仓租约按稳定顺序取得并在退出时逆序释放(tmp_path: Path, monkeypatch) -> None:
    from codev_platform.codegraph import operation_lease

    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    repo_a.mkdir()
    repo_b.mkdir()
    root = tmp_path / "locks"
    events: list[tuple[str, Path]] = []

    @contextmanager
    def fake_lease(repo: Path, *, lock_root: Path | None = None):
        assert lock_root == root
        normalized = Path(repo).resolve()
        events.append(("enter", normalized))
        try:
            yield
        finally:
            events.append(("exit", normalized))

    monkeypatch.setattr(operation_lease, "codegraph_operation_lease", fake_lease)

    with operation_lease.codegraph_operation_leases(
        [repo_b, repo_a, repo_b],
        lock_root=root,
    ):
        assert events == [
            ("enter", repo_a.resolve()),
            ("enter", repo_b.resolve()),
        ]

    assert events == [
        ("enter", repo_a.resolve()),
        ("enter", repo_b.resolve()),
        ("exit", repo_b.resolve()),
        ("exit", repo_a.resolve()),
    ]


def test_多仓租约中途忙碌时必须释放已取得租约(tmp_path: Path, monkeypatch) -> None:
    from codev_platform.codegraph import operation_lease

    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    repo_a.mkdir()
    repo_b.mkdir()
    root = tmp_path / "locks"
    events: list[str] = []

    @contextmanager
    def fake_lease(repo: Path, *, lock_root: Path | None = None):
        if Path(repo).resolve() == repo_b.resolve():
            raise operation_lease.CodegraphOperationLeaseBusyError("正忙")
        events.append("enter-a")
        try:
            yield
        finally:
            events.append("exit-a")

    monkeypatch.setattr(operation_lease, "codegraph_operation_lease", fake_lease)

    with pytest.raises(operation_lease.CodegraphOperationLeaseBusyError):
        with operation_lease.codegraph_operation_leases([repo_a, repo_b], lock_root=root):
            pytest.fail("任一仓租约正忙不得进入恢复临界区")

    assert events == ["enter-a", "exit-a"]
