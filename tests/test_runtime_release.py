"""内容寻址运行时的原子激活与回滚测试。"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform.core.runtime_models import ActivationResult
import codev_platform.runtime_release as runtime_release
from tests.runtime_release_support import (
    BASE_ID as _BASE,
    POSIX_ONLY,
    RELEASE_A as _A,
    RELEASE_B as _B,
    RELEASE_C as _C,
    install_verifier as _install_verifier,
    link_target as _target,
    runtime_root as _runtime_root,
    unlocked as _unlocked,
)


@POSIX_ONLY
def test_first_activation_creates_current_without_previous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)

    result = runtime_release.activate_release(root, _A)

    assert result == ActivationResult(active_release=_A, previous_release=None)
    assert _target(root, "current") == f"releases/{_A}"
    assert _target(root, "previous") is None


@POSIX_ONLY
def test_activation_moves_old_current_to_previous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)
    runtime_release.activate_release(root, _A)

    result = runtime_release.activate_release(root, _B)

    assert result == ActivationResult(active_release=_B, previous_release=_A)
    assert _target(root, "current") == f"releases/{_B}"
    assert _target(root, "previous") == f"releases/{_A}"


@POSIX_ONLY
def test_three_activations_keep_immediate_previous_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)

    runtime_release.activate_release(root, _A)
    runtime_release.activate_release(root, _B)
    result = runtime_release.activate_release(root, _C)

    assert result == ActivationResult(active_release=_C, previous_release=_B)
    assert _target(root, "current") == f"releases/{_C}"
    assert _target(root, "previous") == f"releases/{_B}"


@POSIX_ONLY
def test_same_target_is_idempotent_and_preserves_previous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)
    runtime_release.activate_release(root, _A)
    runtime_release.activate_release(root, _B)

    result = runtime_release.activate_release(root, _B)

    assert result == ActivationResult(active_release=_B, previous_release=_A)
    assert _target(root, "previous") == f"releases/{_A}"


@POSIX_ONLY
def test_rollback_switches_current_and_keeps_previous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)
    runtime_release.activate_release(root, _A)
    runtime_release.activate_release(root, _B)

    result = runtime_release.rollback_release(root)

    assert result == ActivationResult(active_release=_A, previous_release=_A)
    assert _target(root, "current") == f"releases/{_A}"
    assert _target(root, "previous") == f"releases/{_A}"


@POSIX_ONLY
def test_first_rollback_without_previous_preserves_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)
    runtime_release.activate_release(root, _A)

    with pytest.raises(runtime_release.RuntimeReleaseError, match="previous"):
        runtime_release.rollback_release(root)

    assert _target(root, "current") == f"releases/{_A}"


@POSIX_ONLY
def test_broken_previous_fails_without_changing_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)
    runtime_release.activate_release(root, _A)
    (root / "previous").symlink_to("../outside")

    with pytest.raises(runtime_release.RuntimeReleaseError, match="previous"):
        runtime_release.rollback_release(root)

    assert _target(root, "current") == f"releases/{_A}"


@POSIX_ONLY
def test_damaged_previous_release_fails_verification_without_changing_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)
    (root / "current").symlink_to(f"releases/{_B}", target_is_directory=True)
    (root / "previous").symlink_to(f"releases/{_A}", target_is_directory=True)

    def verify_base(_root: Path, base_id: str) -> SimpleNamespace:
        return SimpleNamespace(base_id=base_id)

    def verify(
        _root: Path,
        release_id: str,
        *,
        verified_base: SimpleNamespace,
    ) -> SimpleNamespace:
        assert verified_base.base_id == _BASE
        if release_id == _A:
            raise RuntimeError("注入 previous 完整性损坏")
        return SimpleNamespace(release_id=release_id, base_id=_BASE)

    monkeypatch.setattr(runtime_release, "_verify_base_locked", verify_base)
    monkeypatch.setattr(runtime_release, "_verify_release_locked", verify)

    with pytest.raises(runtime_release.RuntimeReleaseError, match="回滚"):
        runtime_release.rollback_release(root)

    assert _target(root, "current") == f"releases/{_B}"
    assert _target(root, "previous") == f"releases/{_A}"


@POSIX_ONLY
def test_current_replace_failure_preserves_active_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)
    runtime_release.activate_release(root, _A)
    original = runtime_release.os.replace

    def fail_current(source: Path, destination: Path) -> None:
        if Path(destination).name == "current":
            raise OSError("injected replace failure")
        original(source, destination)

    monkeypatch.setattr(runtime_release.os, "replace", fail_current)

    with pytest.raises(runtime_release.RuntimeReleaseError, match="激活"):
        runtime_release.activate_release(root, _B)

    assert _target(root, "current") == f"releases/{_A}"
    assert _target(root, "previous") is None
    assert tuple(root.glob(".current.*.tmp")) == ()

    monkeypatch.setattr(runtime_release.os, "replace", original)
    result = runtime_release.activate_release(root, _B)
    assert result.active_release == _B


@POSIX_ONLY
def test_current_replace_failure_restores_original_previous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)
    (root / "current").symlink_to(f"releases/{_A}", target_is_directory=True)
    (root / "previous").symlink_to(f"releases/{_C}", target_is_directory=True)
    original = runtime_release.os.replace

    def fail_current(source: Path, destination: Path) -> None:
        if Path(destination).name == "current":
            raise OSError("injected replace failure")
        original(source, destination)

    monkeypatch.setattr(runtime_release.os, "replace", fail_current)

    with pytest.raises(runtime_release.RuntimeReleaseError, match="激活"):
        runtime_release.activate_release(root, _B)

    assert _target(root, "current") == f"releases/{_A}"
    assert _target(root, "previous") == f"releases/{_C}"
    assert tuple(root.glob(".*.tmp")) == ()


@POSIX_ONLY
def test_previous_compensation_failure_reports_unprovable_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)
    (root / "current").symlink_to(f"releases/{_A}", target_is_directory=True)
    (root / "previous").symlink_to(f"releases/{_C}", target_is_directory=True)
    original = runtime_release.os.replace
    previous_replacements = 0

    def fail_switch_and_compensation(source: Path, destination: Path) -> None:
        nonlocal previous_replacements
        name = Path(destination).name
        if name == "current":
            raise OSError("injected current failure")
        if name == "previous":
            previous_replacements += 1
            if previous_replacements == 2:
                raise OSError("injected compensation failure")
        original(source, destination)

    monkeypatch.setattr(runtime_release.os, "replace", fail_switch_and_compensation)

    with pytest.raises(
        runtime_release.RuntimeReleaseError,
        match="引用补偿失败，引用状态不可证明",
    ):
        runtime_release.activate_release(root, _B)

    assert _target(root, "current") == f"releases/{_A}"
    assert tuple(root.glob(".*.tmp")) == ()


@POSIX_ONLY
@pytest.mark.parametrize("failed_sync", [1, 2])
def test_link_fsync_failure_restores_both_original_references(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_sync: int,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)
    (root / "current").symlink_to(f"releases/{_A}", target_is_directory=True)
    (root / "previous").symlink_to(f"releases/{_C}", target_is_directory=True)
    original = runtime_release._fsync_directory
    calls = 0

    def fail_once(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == failed_sync:
            raise OSError("injected fsync failure")
        original(path)

    monkeypatch.setattr(runtime_release, "_fsync_directory", fail_once)

    with pytest.raises(runtime_release.RuntimeReleaseError, match="激活"):
        runtime_release.activate_release(root, _B)

    assert _target(root, "current") == f"releases/{_A}"
    assert _target(root, "previous") == f"releases/{_C}"
    assert tuple(root.glob(".*.tmp")) == ()


@POSIX_ONLY
def test_rollback_link_fsync_failure_restores_original_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)
    (root / "current").symlink_to(f"releases/{_B}", target_is_directory=True)
    (root / "previous").symlink_to(f"releases/{_A}", target_is_directory=True)
    original = runtime_release._fsync_directory
    calls = 0

    def fail_once(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected rollback fsync failure")
        original(path)

    monkeypatch.setattr(runtime_release, "_fsync_directory", fail_once)

    with pytest.raises(runtime_release.RuntimeReleaseError, match="回滚"):
        runtime_release.rollback_release(root)

    assert _target(root, "current") == f"releases/{_B}"
    assert _target(root, "previous") == f"releases/{_A}"
    assert tuple(root.glob(".*.tmp")) == ()


@POSIX_ONLY
def test_first_activation_fsync_failure_removes_uncommitted_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)
    original = runtime_release._fsync_directory
    calls = 0

    def fail_once(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected fsync failure")
        original(path)

    monkeypatch.setattr(runtime_release, "_fsync_directory", fail_once)

    with pytest.raises(runtime_release.RuntimeReleaseError, match="激活"):
        runtime_release.activate_release(root, _A)

    assert _target(root, "current") is None
    assert _target(root, "previous") is None


@POSIX_ONLY
def test_each_link_replace_fsyncs_runtime_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)
    synced: list[Path] = []
    monkeypatch.setattr(runtime_release, "_fsync_directory", synced.append)

    runtime_release.activate_release(root, _A)
    runtime_release.activate_release(root, _B)

    assert synced == [root, root, root]


@POSIX_ONLY
def test_activation_verifies_a_shared_base_only_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    (root / "current").symlink_to(f"releases/{_B}", target_is_directory=True)
    (root / "previous").symlink_to(f"releases/{_A}", target_is_directory=True)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)

    @contextmanager
    def id_lock(*_args, **_kwargs):
        yield

    verified: list[str] = []

    def verify_base(_root: Path, base_id: str) -> SimpleNamespace:
        verified.append(base_id)
        return SimpleNamespace(base_id=base_id)

    monkeypatch.setattr(runtime_release, "_id_lock", id_lock)
    monkeypatch.setattr(runtime_release, "_verify_base_locked", verify_base)

    runtime_release.activate_release(root, _C)

    assert verified == [_BASE]


@pytest.mark.parametrize("value", ["a" * 40, "g" * 64, "0" * 64, "../" + "a" * 64])
@POSIX_ONLY
def test_invalid_release_id_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)

    with pytest.raises(runtime_release.RuntimeReleaseError, match="版本 ID"):
        runtime_release.activate_release(root, value)


@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 验证变更动作显式失败关闭")
@pytest.mark.parametrize("action", ["activate", "rollback"])
def test_windows_mutation_fails_closed_before_reading_runtime_root(
    tmp_path: Path,
    action: str,
) -> None:
    root = tmp_path / "不存在的运行时根"

    with pytest.raises(runtime_release.RuntimeReleaseError, match="POSIX"):
        if action == "activate":
            runtime_release.activate_release(root, _A)
        else:
            runtime_release.rollback_release(root)

    assert not root.exists()
