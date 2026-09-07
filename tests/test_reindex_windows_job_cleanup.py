"""Windows Job attempt 后端的契约与真实内核回归。"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from codev_platform.reindex import windows_job as windows_job_module
from codev_platform.reindex.attempt_process import (
    AttemptProcessStartError,
    Deadline,
)

from tests import reindex_windows_job_support as support

def test_windows_start_cleans_suspended_resources_when_handle_build_fails(
    tmp_path: Path,
) -> None:
    native = support._Native()
    native.birth_marker = "invalid-birth-marker"
    clock = support._Clock()

    with pytest.raises(AttemptProcessStartError) as caught:
        support._start(support._backend(native, clock), tmp_path, clock)

    assert caught.value.handle is None
    assert caught.value.retryable is False
    assert "terminate_process" in native.events
    assert {"close_handle:101", "close_handle:102", "close_handle:103"} <= set(native.events)


def test_windows_prepare_runtime_error_after_create_still_discards_all_resources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    native = support._Native()
    clock = support._Clock()
    backend = support._backend(native, clock)

    def fail_build(**_kwargs):
        raise RuntimeError("身份构造替身失败")

    def fail_assign(_job_handle: int, _process_handle: int) -> None:
        native.events.append("assign_process")
        raise RuntimeError("回收分配替身失败")

    monkeypatch.setattr(windows_job_module, "build_owned_windows_job", fail_build)
    monkeypatch.setattr(native, "assign_process", fail_assign)

    with pytest.raises(AttemptProcessStartError) as caught:
        backend.prepare(
            attempt_id="runtime-error-after-create",
            argv=(str(Path(os.sys.executable).resolve()), "-c", "pass"),
            cwd=tmp_path.resolve(),
            bootstrap_log=(tmp_path / "runtime-error-after-create.log").resolve(),
            deadline=Deadline(clock.now + 1.0),
        )

    assert caught.value.handle is None
    assert "terminate_job" in native.events
    assert "terminate_process" in native.events
    for handle in (101, 102, 103):
        assert native.events.count(f"close_handle:{handle}") == 1


def test_windows_prepare_memory_error_cleans_all_resources_before_reraise(
    tmp_path: Path,
    monkeypatch,
) -> None:
    native = support._Native()
    clock = support._Clock()
    backend = support._backend(native, clock)

    def fail_build(**_kwargs):
        raise MemoryError("身份构造内存耗尽替身")

    monkeypatch.setattr(windows_job_module, "build_owned_windows_job", fail_build)

    with pytest.raises(MemoryError, match="内存耗尽"):
        backend.prepare(
            attempt_id="memory-error-after-create",
            argv=(str(Path(os.sys.executable).resolve()), "-c", "pass"),
            cwd=tmp_path.resolve(),
            bootstrap_log=(tmp_path / "memory-error-after-create.log").resolve(),
            deadline=Deadline(clock.now + 1.0),
        )

    assert "terminate_job" in native.events
    assert "terminate_process" in native.events
    for handle in (101, 102, 103):
        assert native.events.count(f"close_handle:{handle}") == 1


def test_windows_prepare_partial_registration_runtime_error_forgets_and_discards_record(
    tmp_path: Path,
    monkeypatch,
) -> None:
    native = support._Native()
    clock = support._Clock()
    backend = support._backend(native, clock)
    remember = backend._resources.remember

    def remember_then_fail(record) -> None:
        remember(record)
        raise RuntimeError("登记替身在写入后失败")

    monkeypatch.setattr(backend._resources, "remember", remember_then_fail)

    with pytest.raises(AttemptProcessStartError) as caught:
        backend.prepare(
            attempt_id="runtime-error-after-registration",
            argv=(str(Path(os.sys.executable).resolve()), "-c", "pass"),
            cwd=tmp_path.resolve(),
            bootstrap_log=(tmp_path / "runtime-error-after-registration.log").resolve(),
            deadline=Deadline(clock.now + 1.0),
        )

    assert caught.value.handle is not None
    assert backend._resources.find(caught.value.handle.native_ref) is None
    assert "terminate_job" in native.events
    assert "terminate_process" in native.events
    for handle in (101, 102, 103):
        assert native.events.count(f"close_handle:{handle}") == 1


def test_windows_prepare_verification_runtime_error_is_bounded_and_releases_record(
    tmp_path: Path,
    monkeypatch,
) -> None:
    native = support._Native()
    clock = support._Clock()
    backend = support._backend(native, clock)

    def fail_verify(_job_handle: int, _process_handle: int) -> None:
        native.events.append("verify_process_in_job")
        raise RuntimeError("归属验证替身失败")

    monkeypatch.setattr(native, "verify_process_in_job", fail_verify)

    with pytest.raises(AttemptProcessStartError) as caught:
        backend.prepare(
            attempt_id="runtime-error-after-verification",
            argv=(str(Path(os.sys.executable).resolve()), "-c", "pass"),
            cwd=tmp_path.resolve(),
            bootstrap_log=(tmp_path / "runtime-error-after-verification.log").resolve(),
            deadline=Deadline(clock.now + 1.0),
        )

    assert caught.value.handle is not None
    assert caught.value.death_proof is not None
    assert backend._resources.find(caught.value.handle.native_ref) is None
    for handle in (101, 102, 103):
        assert native.events.count(f"close_handle:{handle}") == 1
