"""依赖 wheel 下载的并发上限、进度和总截止时间测试。"""

from __future__ import annotations

from pathlib import Path
import threading
import time

import pytest

from codev_platform.runtime_deadline import RuntimeDeadline, RuntimeDeadlineExceeded
from codev_platform.runtime_dependency_contract import DistributionPin
from codev_platform.runtime_lock_download import (
    LockDownloadProgress,
    RuntimeWheelDownloadError,
    download_wheels_bounded,
)


def _pins(count: int) -> tuple[DistributionPin, ...]:
    return tuple(DistributionPin(f"demo-{index}", "1.0") for index in range(count))


def test_downloads_use_bounded_parallelism_and_report_progress(tmp_path: Path) -> None:
    lock = threading.Lock()
    active = 0
    peak = 0
    progress: list[LockDownloadProgress] = []

    def download(pin: DistributionPin, _cancellation) -> Path:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return tmp_path / f"{pin.name}.whl"

    result = download_wheels_bounded(
        _pins(6),
        download,
        deadline=RuntimeDeadline.after(5),
        reporter=progress.append,
        max_workers=2,
        heartbeat_sec=0.01,
    )

    assert peak == 2
    assert [path.name for path in result] == [f"demo-{index}.whl" for index in range(6)]
    completed = [item for item in progress if item.state == "completed"]
    assert completed[-1].completed == 6
    assert completed[-1].total == 6
    assert any(item.state == "waiting" for item in progress)


def test_total_deadline_stops_waiting_for_unbounded_work(tmp_path: Path) -> None:
    def download(pin: DistributionPin, _cancellation) -> Path:
        time.sleep(0.08)
        return tmp_path / f"{pin.name}.whl"

    started = time.monotonic()
    with pytest.raises(RuntimeDeadlineExceeded):
        download_wheels_bounded(
            _pins(2),
            download,
            deadline=RuntimeDeadline.after(0.02),
            max_workers=1,
            heartbeat_sec=0.01,
        )

    assert time.monotonic() - started < 0.5


def test_first_failure_cancels_running_siblings_and_identifies_package(
    tmp_path: Path,
) -> None:
    barrier = threading.Barrier(3)
    cancellation_seen = threading.Event()
    progress: list[LockDownloadProgress] = []

    def download(pin: DistributionPin, cancellation) -> Path:
        barrier.wait(timeout=1)
        if pin.name == "demo-0":
            raise RuntimeError("不得透出的底层错误")
        while not cancellation.wait(0.01):
            pass
        cancellation_seen.set()
        raise RuntimeError("已取消")

    started = time.monotonic()
    with pytest.raises(RuntimeWheelDownloadError) as captured:
        download_wheels_bounded(
            _pins(3),
            download,
            deadline=RuntimeDeadline.after(5),
            reporter=progress.append,
            max_workers=3,
            heartbeat_sec=0.01,
        )

    assert time.monotonic() - started < 0.5
    assert cancellation_seen.is_set()
    assert captured.value.package == "demo-0"
    assert captured.value.code == "runtime_wheel_download_failed"
    assert "底层错误" not in str(captured.value)
    assert any(item.state == "failed" and item.package == "demo-0" for item in progress)
    assert any(item.state == "cancelling" for item in progress)
