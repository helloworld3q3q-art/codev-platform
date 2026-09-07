"""内容寻址运行时激活锁和并发串行化测试。"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform.core.runtime_models import ActivationResult
import codev_platform.runtime_release as runtime_release
from tests.runtime_release_support import (
    POSIX_ONLY,
    RELEASE_A,
    RELEASE_B,
    RELEASE_C,
    install_verifier,
    link_target,
    runtime_root,
)


@POSIX_ONLY
def test_activation_holds_sorted_release_and_base_locks_until_links_are_durable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = runtime_root(tmp_path)
    (root / "current").symlink_to(f"releases/{RELEASE_B}", target_is_directory=True)
    (root / "previous").symlink_to(f"releases/{RELEASE_A}", target_is_directory=True)
    bases = {RELEASE_A: "3" * 64, RELEASE_B: "1" * 64, RELEASE_C: "2" * 64}
    active: set[tuple[str, str]] = set()
    events: list[tuple[str, ...]] = []

    @contextmanager
    def activation_lock(_root: Path):
        events.append(("enter", "activation"))
        active.add(("activation", "global"))
        try:
            yield
        finally:
            active.remove(("activation", "global"))
            events.append(("exit", "activation"))

    @contextmanager
    def id_lock(_root: Path, kind: str, object_id: str, *, shared: bool):
        assert shared is True
        assert ("activation", "global") in active
        events.append(("enter", kind, object_id))
        active.add((kind, object_id))
        try:
            yield
        finally:
            active.remove((kind, object_id))
            events.append(("exit", kind, object_id))

    expected_release_locks = {
        ("release", item) for item in (RELEASE_A, RELEASE_B, RELEASE_C)
    }
    expected_base_locks = {("base", item) for item in bases.values()}

    def read_base_id(_root: Path, release_id: str) -> str:
        assert expected_release_locks <= active
        events.append(("read", release_id))
        return bases[release_id]

    def verify_base(_root: Path, base_id: str) -> SimpleNamespace:
        events.append(("verify-base", base_id))
        return SimpleNamespace(base_id=base_id)

    def verify(
        _root: Path,
        release_id: str,
        *,
        verified_base: SimpleNamespace,
    ) -> SimpleNamespace:
        assert expected_release_locks | expected_base_locks <= active
        assert verified_base.base_id == bases[release_id]
        events.append(("verify", release_id))
        return SimpleNamespace(release_id=release_id, base_id=bases[release_id])

    original_replace = runtime_release._replace_link

    def replace(target_root: Path, name: str, release_id: str) -> None:
        assert expected_release_locks | expected_base_locks <= active
        events.append(("replace", name, release_id))
        original_replace(target_root, name, release_id)

    monkeypatch.setattr(runtime_release, "_activation_lock", activation_lock)
    monkeypatch.setattr(runtime_release, "_id_lock", id_lock)
    monkeypatch.setattr(runtime_release, "_read_release_base_id_locked", read_base_id)
    monkeypatch.setattr(runtime_release, "_verify_base_locked", verify_base)
    monkeypatch.setattr(runtime_release, "_verify_release_locked", verify)
    monkeypatch.setattr(runtime_release, "_replace_link", replace)

    result = runtime_release.activate_release(root, RELEASE_C)

    assert result == ActivationResult(active_release=RELEASE_C, previous_release=RELEASE_B)
    entered = [event for event in events if event[0] == "enter"]
    assert entered == [
        ("enter", "activation"),
        ("enter", "release", RELEASE_A),
        ("enter", "release", RELEASE_B),
        ("enter", "release", RELEASE_C),
        ("enter", "base", "1" * 64),
        ("enter", "base", "2" * 64),
        ("enter", "base", "3" * 64),
    ]
    assert [event for event in events if event[0] == "verify"] == [
        ("verify", RELEASE_A),
        ("verify", RELEASE_B),
        ("verify", RELEASE_C),
    ]
    assert [event for event in events if event[0] == "verify-base"] == [
        ("verify-base", "1" * 64),
        ("verify-base", "2" * 64),
        ("verify-base", "3" * 64),
    ]
    last_replace = max(index for index, event in enumerate(events) if event[0] == "replace")
    first_exit = min(index for index, event in enumerate(events) if event[0] == "exit")
    assert last_replace < first_exit


@POSIX_ONLY
def test_rollback_holds_current_and_previous_locks_through_current_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = runtime_root(tmp_path)
    (root / "current").symlink_to(f"releases/{RELEASE_B}", target_is_directory=True)
    (root / "previous").symlink_to(f"releases/{RELEASE_A}", target_is_directory=True)
    bases = {RELEASE_A: "2" * 64, RELEASE_B: "1" * 64}
    active: set[tuple[str, str]] = set()
    events: list[tuple[str, ...]] = []

    @contextmanager
    def activation_lock(_root: Path):
        active.add(("activation", "global"))
        try:
            yield
        finally:
            active.remove(("activation", "global"))

    @contextmanager
    def id_lock(_root: Path, kind: str, object_id: str, *, shared: bool):
        assert shared and ("activation", "global") in active
        events.append(("enter", kind, object_id))
        active.add((kind, object_id))
        try:
            yield
        finally:
            active.remove((kind, object_id))

    def read_base_id(_root: Path, release_id: str) -> str:
        assert {("release", RELEASE_A), ("release", RELEASE_B)} <= active
        return bases[release_id]

    def verify_base(_root: Path, base_id: str) -> SimpleNamespace:
        events.append(("verify-base", base_id))
        return SimpleNamespace(base_id=base_id)

    def verify(
        _root: Path,
        release_id: str,
        *,
        verified_base: SimpleNamespace,
    ) -> SimpleNamespace:
        assert {
            ("release", RELEASE_A),
            ("release", RELEASE_B),
            ("base", "1" * 64),
            ("base", "2" * 64),
        } <= active
        assert verified_base.base_id == bases[release_id]
        events.append(("verify", release_id))
        return SimpleNamespace(release_id=release_id, base_id=bases[release_id])

    original_replace = runtime_release._replace_link

    def replace(target_root: Path, name: str, release_id: str) -> None:
        assert len(active) == 5
        events.append(("replace", name, release_id))
        original_replace(target_root, name, release_id)

    monkeypatch.setattr(runtime_release, "_activation_lock", activation_lock)
    monkeypatch.setattr(runtime_release, "_id_lock", id_lock)
    monkeypatch.setattr(runtime_release, "_read_release_base_id_locked", read_base_id)
    monkeypatch.setattr(runtime_release, "_verify_base_locked", verify_base)
    monkeypatch.setattr(runtime_release, "_verify_release_locked", verify)
    monkeypatch.setattr(runtime_release, "_replace_link", replace)

    result = runtime_release.rollback_release(root)

    assert result == ActivationResult(active_release=RELEASE_A, previous_release=RELEASE_A)
    assert [event for event in events if event[0] == "enter"] == [
        ("enter", "release", RELEASE_A),
        ("enter", "release", RELEASE_B),
        ("enter", "base", "1" * 64),
        ("enter", "base", "2" * 64),
    ]
    assert ("replace", "current", RELEASE_A) in events


@POSIX_ONLY
def test_concurrent_activations_keep_current_previous_as_one_serial_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = runtime_root(tmp_path)
    install_verifier(monkeypatch)
    runtime_release.activate_release(root, RELEASE_A)
    ready = threading.Barrier(3)

    def activate(release_id: str) -> ActivationResult:
        ready.wait(timeout=5)
        return runtime_release.activate_release(root, release_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(activate, RELEASE_B)
        second = executor.submit(activate, RELEASE_C)
        ready.wait(timeout=5)
        results = (first.result(timeout=10), second.result(timeout=10))

    current = link_target(root, "current")
    previous = link_target(root, "previous")
    assert {result.active_release for result in results} == {RELEASE_B, RELEASE_C}
    assert {current, previous} == {
        f"releases/{RELEASE_B}",
        f"releases/{RELEASE_C}",
    }
