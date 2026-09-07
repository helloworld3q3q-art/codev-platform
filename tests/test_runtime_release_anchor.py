"""显式 target 与 rollback anchor 的原子激活事务测试。"""

from __future__ import annotations

import os
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
def test_显式回滚锚点不会把旧current自动写入previous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)
    (root / "current").symlink_to(f"releases/{_A}", target_is_directory=True)

    result = runtime_release.activate_release_with_rollback_anchor(root, _B, _C)

    assert result == ActivationResult(active_release=_B, previous_release=_C)
    assert _target(root, "current") == f"releases/{_B}"
    assert _target(root, "previous") == f"releases/{_C}"


@POSIX_ONLY
def test_显式激活幂等且只深验target与rollback_anchor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    (root / "current").symlink_to(f"releases/{_A}", target_is_directory=True)
    verified: list[str] = []
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)
    monkeypatch.setattr(runtime_release, "_read_release_base_id_locked", lambda *_args: _BASE)
    monkeypatch.setattr(
        runtime_release,
        "_verify_base_locked",
        lambda _root, base_id: SimpleNamespace(base_id=base_id),
    )

    def verify(
        _root: Path,
        release_id: str,
        *,
        verified_base: SimpleNamespace,
    ) -> SimpleNamespace:
        assert verified_base.base_id == _BASE
        verified.append(release_id)
        return SimpleNamespace(release_id=release_id, base_id=_BASE)

    monkeypatch.setattr(runtime_release, "_verify_release_locked", verify)

    first = runtime_release.activate_release_with_rollback_anchor(root, _B, _C)
    second = runtime_release.activate_release_with_rollback_anchor(root, _B, _C)

    assert first == second == ActivationResult(active_release=_B, previous_release=_C)
    assert verified == [_B, _C, _B, _C]
    assert _A not in verified


@POSIX_ONLY
def test_显式激活可覆盖悬空旧引用且只验证新双引用(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)
    dangling_current = "e" * 64
    dangling_previous = "f" * 64
    (root / "current").symlink_to(
        f"releases/{dangling_current}",
        target_is_directory=True,
    )
    (root / "previous").symlink_to(
        f"releases/{dangling_previous}",
        target_is_directory=True,
    )

    result = runtime_release.activate_release_with_rollback_anchor(root, _B, _C)

    assert result == ActivationResult(active_release=_B, previous_release=_C)
    assert _target(root, "current") == f"releases/{_B}"
    assert _target(root, "previous") == f"releases/{_C}"


@POSIX_ONLY
def test_target与rollback_anchor不同基座时切换前失败(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    other_base = "e" * 64
    _install_verifier(monkeypatch, {_A: _BASE, _B: _BASE, _C: other_base})
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)
    (root / "current").symlink_to(f"releases/{_A}", target_is_directory=True)

    with pytest.raises(runtime_release.RuntimeReleaseError, match="同一基座"):
        runtime_release.activate_release_with_rollback_anchor(root, _B, _C)

    assert _target(root, "current") == f"releases/{_A}"
    assert _target(root, "previous") is None


@pytest.mark.parametrize("failed_sync", [1, 2])
@POSIX_ONLY
def test_显式激活任一目录提交失败都恢复原始双引用(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_sync: int,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)
    (root / "current").symlink_to(f"releases/{_A}", target_is_directory=True)
    original = runtime_release._fsync_directory
    calls = 0

    def fail_once(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == failed_sync:
            raise OSError("injected anchor fsync failure")
        original(path)

    monkeypatch.setattr(runtime_release, "_fsync_directory", fail_once)

    with pytest.raises(runtime_release.RuntimeReleaseError, match="显式回滚锚点激活"):
        runtime_release.activate_release_with_rollback_anchor(root, _B, _C)

    assert _target(root, "current") == f"releases/{_A}"
    assert _target(root, "previous") is None
    assert tuple(root.glob(".*.tmp")) == ()


@POSIX_ONLY
def test_显式激活current替换失败时撤销已提交的rollback_anchor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)
    (root / "current").symlink_to(f"releases/{_A}", target_is_directory=True)
    original = runtime_release.os.replace

    def fail_current(source: Path, destination: Path) -> None:
        if Path(destination).name == "current":
            raise OSError("injected current replace failure")
        original(source, destination)

    monkeypatch.setattr(runtime_release.os, "replace", fail_current)

    with pytest.raises(runtime_release.RuntimeReleaseError, match="显式回滚锚点激活"):
        runtime_release.activate_release_with_rollback_anchor(root, _B, _C)

    assert _target(root, "current") == f"releases/{_A}"
    assert _target(root, "previous") is None


@pytest.mark.parametrize("failed_name", ["previous", "current"])
@POSIX_ONLY
def test_显式激活任一引用替换失败都恢复原始双引用且错误脱敏(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_name: str,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)
    (root / "current").symlink_to(f"releases/{_A}", target_is_directory=True)
    (root / "previous").symlink_to(f"releases/{_A}", target_is_directory=True)
    original = runtime_release.os.replace
    failed = False

    def fail_once(source: Path, destination: Path) -> None:
        nonlocal failed
        if not failed and Path(destination).name == failed_name:
            failed = True
            raise OSError(f"sensitive {failed_name} replace failure")
        original(source, destination)

    monkeypatch.setattr(runtime_release.os, "replace", fail_once)

    with pytest.raises(runtime_release.RuntimeReleaseError) as caught:
        runtime_release.activate_release_with_rollback_anchor(root, _B, _C)

    assert str(caught.value) == "运行时显式回滚锚点激活失败"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert _target(root, "current") == f"releases/{_A}"
    assert _target(root, "previous") == f"releases/{_A}"


@POSIX_ONLY
def test_显式激活临时链接创建失败不残留且错误脱敏(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)
    (root / "current").symlink_to(f"releases/{_A}", target_is_directory=True)
    original = Path.symlink_to

    def fail_temporary(path: Path, target: str, *args: object, **kwargs: object) -> None:
        if path.name.startswith(".previous."):
            raise OSError("sensitive temporary link failure")
        original(path, target, *args, **kwargs)

    monkeypatch.setattr(Path, "symlink_to", fail_temporary)

    with pytest.raises(runtime_release.RuntimeReleaseError) as caught:
        runtime_release.activate_release_with_rollback_anchor(root, _B, _C)

    assert str(caught.value) == "运行时显式回滚锚点激活失败"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert _target(root, "current") == f"releases/{_A}"
    assert _target(root, "previous") is None
    assert tuple(root.glob(".*.tmp")) == ()


@POSIX_ONLY
def test_显式激活补偿失败时固定报告状态不可证明且错误脱敏(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)
    (root / "current").symlink_to(f"releases/{_A}", target_is_directory=True)
    original = runtime_release.os.replace

    def fail_current(source: Path, destination: Path) -> None:
        if Path(destination).name == "current":
            raise OSError("sensitive activation failure")
        original(source, destination)

    monkeypatch.setattr(runtime_release.os, "replace", fail_current)
    monkeypatch.setattr(
        runtime_release,
        "_restore_explicit_link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("sensitive compensation failure")),
        raising=False,
    )

    with pytest.raises(runtime_release.RuntimeReleaseError) as caught:
        runtime_release.activate_release_with_rollback_anchor(root, _B, _C)

    assert str(caught.value) == ("运行时显式回滚锚点激活失败且引用补偿失败，引用状态不可证明")
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    ("current", "previous"),
    [
        (_A, _C),
        (_B, _A),
        (_B, _C),
    ],
    ids=["已提交previous", "已提交current", "双引用已提交"],
)
@POSIX_ONLY
def test_显式激活可从硬退出留下的混合引用幂等续跑(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    current: str,
    previous: str,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)
    (root / "current").symlink_to(f"releases/{current}", target_is_directory=True)
    (root / "previous").symlink_to(f"releases/{previous}", target_is_directory=True)

    result = runtime_release.activate_release_with_rollback_anchor(root, _B, _C)

    assert result == ActivationResult(active_release=_B, previous_release=_C)
    assert _target(root, "current") == f"releases/{_B}"
    assert _target(root, "previous") == f"releases/{_C}"


@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 验证变更动作显式失败关闭")
def test_Windows显式回滚锚点激活在读取根目录前失败关闭(tmp_path: Path) -> None:
    root = tmp_path / "不存在的运行时根"

    with pytest.raises(runtime_release.RuntimeReleaseError, match="POSIX"):
        runtime_release.activate_release_with_rollback_anchor(root, _B, _C)

    assert not root.exists()


@POSIX_ONLY
def test_预期current激活拒绝覆盖并发发布链(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)
    (root / "current").symlink_to(f"releases/{_C}", target_is_directory=True)

    with pytest.raises(runtime_release.RuntimeReleaseError, match="其他发布链"):
        runtime_release.activate_release_from_expected_current(root, _B, _A)

    assert _target(root, "current") == f"releases/{_C}"
    assert _target(root, "previous") is None


@POSIX_ONLY
def test_预期current激活与回滚形成确定性双引用(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    _install_verifier(monkeypatch)
    monkeypatch.setattr(runtime_release, "_activation_lock", _unlocked)
    (root / "current").symlink_to(f"releases/{_A}", target_is_directory=True)
    (root / "previous").symlink_to(f"releases/{_C}", target_is_directory=True)

    activated = runtime_release.activate_release_from_expected_current(root, _B, _A)

    assert activated == ActivationResult(active_release=_B, previous_release=_A)
    assert _target(root, "current") == f"releases/{_B}"
    assert _target(root, "previous") == f"releases/{_A}"

    rolled_back = runtime_release.rollback_release_from_expected_current(root, _B, _A)

    assert rolled_back == ActivationResult(active_release=_A, previous_release=_A)
    assert _target(root, "current") == f"releases/{_A}"
    assert _target(root, "previous") == f"releases/{_A}"
