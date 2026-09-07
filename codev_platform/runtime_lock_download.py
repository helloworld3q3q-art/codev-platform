"""依赖 wheel 下载的有界并发、总截止时间与安全进度编排。"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextvars import copy_context
from dataclasses import dataclass
from pathlib import Path

from codev_platform.runtime_cancellable_process import RuntimeCancellation
from codev_platform.runtime_deadline import RuntimeDeadline, RuntimeDeadlineExceeded
from codev_platform.runtime_dependency_contract import DistributionPin


@dataclass(frozen=True, slots=True)
class LockDownloadProgress:
    """不含 URL、路径或凭据的依赖准备进度。"""

    completed: int
    total: int
    state: str
    package: str | None = None
    active: int = 0


ProgressReporter = Callable[[LockDownloadProgress], None]
WheelDownloader = Callable[[DistributionPin, RuntimeCancellation], Path]


class RuntimeWheelDownloadError(RuntimeError):
    """一批 wheel 中某个规范包下载失败；不泄漏命令、URL 或环境。"""

    code = "runtime_wheel_download_failed"

    def __init__(self, package: str) -> None:
        self.package = package
        super().__init__(f"依赖 wheel 下载失败：包={package}，错误码={self.code}")


def download_wheels_bounded(
    pins: tuple[DistributionPin, ...],
    downloader: WheelDownloader,
    *,
    deadline: RuntimeDeadline,
    reporter: ProgressReporter | None = None,
    max_workers: int = 4,
    heartbeat_sec: float = 15.0,
) -> tuple[Path, ...]:
    """并发准备互不依赖的 wheel；任一失败即取消尚未开始的任务。"""
    if not pins or not callable(downloader):
        raise ValueError("依赖下载输入无效")
    if type(max_workers) is not int or not 1 <= max_workers <= 8:
        raise ValueError("依赖下载并发数无效")
    if not isinstance(deadline, RuntimeDeadline):
        raise TypeError("依赖下载截止时间无效")
    total = len(pins)
    report = _noop_reporter if reporter is None else reporter
    if not callable(report):
        raise TypeError("依赖下载进度适配器无效")
    executor = ThreadPoolExecutor(
        max_workers=min(max_workers, total),
        thread_name_prefix="runtime-wheel",
    )
    cancellation = RuntimeCancellation()
    futures: dict[Future[Path], DistributionPin] = {}
    completed: dict[str, Path] = {}
    failure_package: str | None = None
    try:
        for pin in pins:
            context = copy_context()
            future = executor.submit(
                context.run,
                _download_one,
                pin,
                downloader,
                cancellation,
                report,
                total,
            )
            futures[future] = pin
        pending = set(futures)
        while pending:
            timeout = min(float(heartbeat_sec), deadline.remaining())
            done, pending = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
            if not done:
                _emit(
                    report,
                    LockDownloadProgress(
                        completed=len(completed),
                        total=total,
                        state="waiting",
                        active=min(max_workers, len(pending)),
                    )
                )
                continue
            for future in sorted(done, key=lambda item: futures[item].name):
                pin = futures[future]
                try:
                    completed[pin.name] = future.result()
                except Exception:
                    failure_package = pin.name
                    _emit(
                        report,
                        LockDownloadProgress(
                            completed=len(completed),
                            total=total,
                            state="failed",
                            package=pin.name,
                            active=sum(not item.done() for item in futures),
                        ),
                    )
                    raise RuntimeWheelDownloadError(pin.name) from None
                _emit(
                    report,
                    LockDownloadProgress(
                        completed=len(completed),
                        total=total,
                        state="completed",
                        package=pin.name,
                        active=min(max_workers, len(pending)),
                    )
                )
        return tuple(completed[pin.name] for pin in pins)
    except BaseException:
        cancellation.cancel()
        for future in futures:
            future.cancel()
        _drain_cancelled_downloads(
            futures,
            report,
            completed=len(completed),
            total=total,
            package=failure_package,
            heartbeat_sec=heartbeat_sec,
        )
        raise
    finally:
        cancellation.cancel()
        # worker 内的子进程 timeout 受同一 deadline 约束；这里等待它们完成清算，
        # 避免函数返回后仍有下载线程继续写 wheelhouse。
        executor.shutdown(wait=True, cancel_futures=True)


def _download_one(
    pin: DistributionPin,
    downloader: WheelDownloader,
    cancellation: RuntimeCancellation,
    reporter: ProgressReporter,
    total: int,
) -> Path:
    _emit(
        reporter,
        LockDownloadProgress(
            completed=0,
            total=total,
            state="started",
            package=pin.name,
        )
    )
    return downloader(pin, cancellation)


def _drain_cancelled_downloads(
    futures: dict[Future[Path], DistributionPin],
    reporter: ProgressReporter,
    *,
    completed: int,
    total: int,
    package: str | None,
    heartbeat_sec: float,
) -> None:
    """取消后仍等待已启动任务完成进程回收，并持续发出安全心跳。"""
    pending = {future for future in futures if not future.done()}
    if pending:
        _emit(
            reporter,
            LockDownloadProgress(
                completed=completed,
                total=total,
                state="cancelling",
                package=package,
                active=len(pending),
            ),
        )
    while pending:
        _done, pending = wait(
            pending,
            timeout=float(heartbeat_sec),
            return_when=FIRST_COMPLETED,
        )
        if pending:
            _emit(
                reporter,
                LockDownloadProgress(
                    completed=completed,
                    total=total,
                    state="cancelling",
                    package=package,
                    active=len(pending),
                ),
            )


def _emit(reporter: ProgressReporter, progress: LockDownloadProgress) -> None:
    """进度适配器故障不得破坏下载事务或掩盖原始失败。"""
    try:
        reporter(progress)
    except Exception:
        return


def _noop_reporter(_progress: LockDownloadProgress) -> None:
    return None


__all__ = [
    "LockDownloadProgress",
    "ProgressReporter",
    "RuntimeDeadlineExceeded",
    "RuntimeWheelDownloadError",
    "download_wheels_bounded",
]
