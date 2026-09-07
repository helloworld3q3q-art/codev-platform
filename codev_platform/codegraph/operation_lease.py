"""CodeGraph 重建与 MCP 后端共享的跨进程操作租约。

CodeGraph 的 MCP ``serve`` 在首次请求时可能执行追赶同步，因此它与 ``codegraph
sync`` 不是读写可并行关系。本模块按规范化仓根生成稳定租约文件：重建命令在整个
``sync`` 子进程期间持有它，MCP 后端在整个 stdio 会话期间持有它。任一方忙碌时，另一方
必须重试，不能依赖 SQLite 锁的偶然时序。
"""
from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from pathlib import Path

from codev_platform.core.paths import data_root
from codev_platform.reindex.file_advisory_lock import advisory_lock


_LEASE_DIRECTORY_NAME = "codegraph-operation-leases"
_WRITER_INTENT_DIRECTORY_NAME = "writer-intents"


class CodegraphOperationLeaseError(RuntimeError):
    """CodeGraph 操作租约无法安全取得或验证。"""


class CodegraphOperationLeaseBusyError(CodegraphOperationLeaseError):
    """已有重建或 MCP 后端持有同一仓的操作租约。"""


class _ImmediateDeadline:
    """操作租约不等待，调用方保留自己的有限重试与退避策略。"""

    def check(self) -> None:
        return None

    def remaining(self) -> float:
        return 0.0


class _CoordinationDeadline:
    """为全部写意图与操作租约共享一个有限等待预算。"""

    def __init__(self, timeout_sec: float) -> None:
        try:
            timeout = float(timeout_sec)
        except (TypeError, ValueError, OverflowError):
            raise CodegraphOperationLeaseError("CodeGraph 协调超时必须为有限非负数") from None
        if not math.isfinite(timeout) or timeout < 0:
            raise CodegraphOperationLeaseError("CodeGraph 协调超时必须为有限非负数")
        self._expires_at = time.monotonic() + timeout
        if not math.isfinite(self._expires_at):
            raise CodegraphOperationLeaseError("CodeGraph 协调超时必须为有限非负数")

    def check(self) -> None:
        if self.remaining() <= 0:
            raise CodegraphOperationLeaseBusyError("CodeGraph 协调租约等待超时")

    def remaining(self) -> float:
        return max(0.0, self._expires_at - time.monotonic())


class _FirstAttemptDeadline:
    """每把锁仅放行首次立即尝试，后续检查共用同一截止时间。"""

    def __init__(self, shared: _CoordinationDeadline) -> None:
        self._shared = shared
        self._first_check = True

    def check(self) -> None:
        if self._first_check:
            self._first_check = False
            return
        self._shared.check()

    def remaining(self) -> float:
        return self._shared.remaining()


def lease_path_for_repo(
    repo: Path | str,
    *,
    lock_root: Path | None = None,
) -> Path:
    """返回仓根对应的稳定租约路径，但不创建任何目录或文件。"""
    normalized_repo = _normalize_repo(repo)
    normalized_root = _normalize_lock_root(lock_root)
    digest = hashlib.sha256(
        str(normalized_repo).encode("utf-8", "surrogatepass")
    ).hexdigest()
    return normalized_root / f"{digest}.lock"


@contextmanager
def codegraph_operation_lease(
    repo: Path | str,
    *,
    lock_root: Path | None = None,
) -> Iterator[None]:
    """非阻塞取得仓级租约；忙碌或任何不确定性均失败关闭。"""
    path = lease_path_for_repo(repo, lock_root=lock_root)
    body_raised = False
    try:
        with advisory_lock(path, _ImmediateDeadline(), blocking=False) as acquired:
            if acquired is not True:
                raise CodegraphOperationLeaseBusyError("CodeGraph 操作租约正忙")
            try:
                yield
            except BaseException:
                body_raised = True
                raise
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except CodegraphOperationLeaseError:
        raise
    except Exception:
        if body_raised:
            raise
        raise CodegraphOperationLeaseError("CodeGraph 操作租约不可用") from None


@contextmanager
def codegraph_operation_leases(
    repositories: Iterable[Path | str],
    *,
    lock_root: Path | None = None,
) -> Iterator[None]:
    """按稳定全序取得多个仓的租约，避免跨仓恢复与重建形成死锁。"""
    ordered_repositories = _ordered_unique_repositories(repositories, lock_root=lock_root)
    with ExitStack() as stack:
        for repo in ordered_repositories:
            stack.enter_context(codegraph_operation_lease(repo, lock_root=lock_root))
        yield


def codegraph_writer_pending(
    repo: Path | str,
    *,
    lock_root: Path | None = None,
) -> bool:
    """返回同一仓是否已有跨进程写意图；探测本身不保留锁。"""
    try:
        path = _writer_intent_path_for_repo(repo, lock_root=lock_root)
        with advisory_lock(path, _ImmediateDeadline(), blocking=False) as acquired:
            return acquired is not True
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except CodegraphOperationLeaseError:
        raise
    except Exception:
        raise CodegraphOperationLeaseError("CodeGraph 写意图不可用") from None


def codegraph_reindex_leases(
    repositories: Iterable[Path | str],
    *,
    timeout_sec: float = 15.0,
    lock_root: Path | None = None,
) -> AbstractContextManager[None]:
    """返回先锁定全部写意图、再锁定全部操作租约的协调上下文。"""
    return _codegraph_reindex_leases(
        repositories,
        timeout_sec=timeout_sec,
        lock_root=lock_root,
    )


@contextmanager
def _codegraph_reindex_leases(
    repositories: Iterable[Path | str],
    *,
    timeout_sec: float,
    lock_root: Path | None,
) -> Iterator[None]:
    body_raised = False
    try:
        ordered_repositories = _ordered_unique_repositories(
            repositories,
            lock_root=lock_root,
        )
        deadline = _CoordinationDeadline(timeout_sec)
        with ExitStack() as stack:
            for repo in ordered_repositories:
                _enter_coordination_lock(
                    stack,
                    _writer_intent_path_for_repo(repo, lock_root=lock_root),
                    deadline,
                )
            for repo in ordered_repositories:
                _enter_coordination_lock(
                    stack,
                    lease_path_for_repo(repo, lock_root=lock_root),
                    deadline,
                )
            try:
                yield
            except BaseException:
                body_raised = True
                raise
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except CodegraphOperationLeaseError:
        raise
    except Exception:
        if body_raised:
            raise
        raise CodegraphOperationLeaseError("CodeGraph 重建协调租约不可用") from None


def _enter_coordination_lock(
    stack: ExitStack,
    path: Path,
    deadline: _CoordinationDeadline,
) -> None:
    acquired = stack.enter_context(
        advisory_lock(path, _FirstAttemptDeadline(deadline), blocking=True)
    )
    if acquired is not True:
        raise CodegraphOperationLeaseBusyError("CodeGraph 协调租约正忙")


def _writer_intent_path_for_repo(
    repo: Path | str,
    *,
    lock_root: Path | None,
) -> Path:
    operation_path = lease_path_for_repo(repo, lock_root=lock_root)
    return operation_path.parent / _WRITER_INTENT_DIRECTORY_NAME / operation_path.name


def _normalize_repo(value: Path | str) -> Path:
    try:
        repo = Path(value)
        if not repo.is_absolute():
            raise CodegraphOperationLeaseError("CodeGraph 仓路径必须为绝对路径")
        normalized = repo.resolve(strict=True)
    except CodegraphOperationLeaseError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise CodegraphOperationLeaseError("CodeGraph 仓路径不可用") from None
    if not normalized.is_dir():
        raise CodegraphOperationLeaseError("CodeGraph 仓路径不可用")
    return normalized


def _normalize_lock_root(value: Path | None) -> Path:
    try:
        root = data_root() / _LEASE_DIRECTORY_NAME if value is None else Path(value)
    except (OSError, TypeError, ValueError):
        raise CodegraphOperationLeaseError("CodeGraph 租约路径不可用") from None
    if not root.is_absolute():
        raise CodegraphOperationLeaseError("CodeGraph 租约路径必须为绝对路径")
    return root


def _ordered_unique_repositories(
    repositories: Iterable[Path | str],
    *,
    lock_root: Path | None,
) -> list[Path]:
    if isinstance(repositories, (str, Path)):
        raise CodegraphOperationLeaseError("CodeGraph 仓集合必须为可迭代路径集合")
    try:
        items = list(repositories)
    except TypeError:
        raise CodegraphOperationLeaseError("CodeGraph 仓集合不可用") from None
    unique: dict[Path, Path] = {}
    for value in items:
        path = lease_path_for_repo(value, lock_root=lock_root)
        unique.setdefault(path, _normalize_repo(value))
    if not unique:
        raise CodegraphOperationLeaseError("CodeGraph 仓集合不能为空")
    return sorted(unique.values(), key=str)


__all__ = [
    "CodegraphOperationLeaseBusyError",
    "CodegraphOperationLeaseError",
    "codegraph_operation_lease",
    "codegraph_operation_leases",
    "codegraph_reindex_leases",
    "codegraph_writer_pending",
    "lease_path_for_repo",
]
