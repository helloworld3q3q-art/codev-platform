"""当前 release 的只读身份绑定与锁生命周期测试。"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from codev_platform.core.runtime_models import (
    RUNTIME_ACCESS_PROFILE,
    BaseMetadata,
    ReleaseMetadata,
    RuntimeAbi,
)
import codev_platform.runtime_release_binding as binding


_RELEASE_ID = "1" * 64
_BASE_ID = "2" * 64
_REVISION = "a" * 40
_REQUIREMENTS_SHA = "3" * 64


def _base() -> BaseMetadata:
    return BaseMetadata(
        schema_version=3,
        access_profile=RUNTIME_ACCESS_PROFILE,
        base_id=_BASE_ID,
        requirements_sha256=_REQUIREMENTS_SHA,
        approved_index_url="https://download.pytorch.org/whl/cu128",
        artifact_manifest_sha256="4" * 64,
        freeze_sha256="5" * 64,
        purelib_inventory_sha256="6" * 64,
        abi=RuntimeAbi(
            implementation="cpython",
            python_version="3.12.4",
            cache_tag="cpython-312",
            soabi="cpython-312-x86_64-linux-gnu",
            platform_tag="linux-x86_64",
            machine="x86_64",
        ),
        created_at="2026-07-17T00:00:00Z",
        python_relative="venv/bin/python",
        purelib_relative="venv/lib/python3.12/site-packages",
        bin_relative="venv/bin",
        lock_relative="requirements.lock",
    )


def _release() -> ReleaseMetadata:
    return ReleaseMetadata(
        schema_version=1,
        release_id=_RELEASE_ID,
        runtime_revision=_REVISION,
        wheel_sha256="6" * 64,
        base_id=_BASE_ID,
        base_requirements_sha256=_REQUIREMENTS_SHA,
        base_metadata_sha256="7" * 64,
        app_freeze_sha256="8" * 64,
        created_at="2026-07-17T00:00:00Z",
        python_relative="venv/bin/python",
        purelib_relative="venv/lib/python3.12/site-packages",
        base_link_relative="base",
        base_pth_relative="venv/lib/python3.12/site-packages/codev_platform_base.pth",
    )


def _install_verified_fakes(
    binding,
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    *,
    read_current=None,
    base: BaseMetadata | None = None,
    release: ReleaseMetadata | None = None,
) -> None:
    current_reader = read_current or (lambda _root: _RELEASE_ID)
    monkeypatch.setattr(binding, "_canonical_runtime_root", lambda value: Path(value))
    monkeypatch.setattr(
        binding,
        "_activation_lock",
        lambda _root, *, shared: nullcontext() if shared else pytest.fail("必须使用共享锁"),
    )
    monkeypatch.setattr(
        binding,
        "_id_lock",
        lambda _root, _kind, _object_id, *, shared: (
            nullcontext() if shared else pytest.fail("必须使用共享锁")
        ),
    )
    monkeypatch.setattr(binding, "_read_current_release_id", current_reader)
    monkeypatch.setattr(
        binding,
        "_read_release_base_id_locked",
        lambda _root, _release_id: _BASE_ID,
    )
    monkeypatch.setattr(binding, "_verify_base_locked", lambda _root, _base_id: base or _base())
    monkeypatch.setattr(
        binding,
        "_verify_release_locked",
        lambda _root, _release_id, *, verified_base: release or _release(),
    )


def test_bind_current_release_holds_locks_in_fixed_order_until_context_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    @contextmanager
    def activation(root: Path, *, shared: bool):
        assert root == tmp_path
        assert shared is True
        events.append("enter:activation")
        try:
            yield
        finally:
            events.append("exit:activation")

    @contextmanager
    def identity_lock(root: Path, kind: str, object_id: str, *, shared: bool):
        assert root == tmp_path
        assert shared is True
        events.append(f"enter:{kind}:{object_id}")
        try:
            yield
        finally:
            events.append(f"exit:{kind}:{object_id}")

    current_reads = 0

    def read_current(root: Path) -> str:
        nonlocal current_reads
        assert root == tmp_path
        current_reads += 1
        events.append(f"read:current:{current_reads}")
        return _RELEASE_ID

    monkeypatch.setattr(binding, "_canonical_runtime_root", lambda root: Path(root))
    monkeypatch.setattr(binding, "_activation_lock", activation)
    monkeypatch.setattr(binding, "_id_lock", identity_lock)
    monkeypatch.setattr(binding, "_read_current_release_id", read_current)
    monkeypatch.setattr(
        binding,
        "_read_release_base_id_locked",
        lambda _root, _release_id: (events.append("read:base_id"), _BASE_ID)[1],
    )
    monkeypatch.setattr(
        binding,
        "_verify_base_locked",
        lambda _root, _base_id: (events.append("verify:base"), _base())[1],
    )
    monkeypatch.setattr(
        binding,
        "_verify_release_locked",
        lambda _root, _release_id, *, verified_base: (
            events.append("verify:release"),
            _release(),
        )[1],
    )

    with binding.bind_current_release(tmp_path, _RELEASE_ID, _REVISION) as selected:
        assert selected.root == tmp_path
        assert selected.release_id == _RELEASE_ID
        assert selected.runtime_revision == _REVISION
        assert selected.base_id == _BASE_ID
        assert selected.interpreter_path == (
            tmp_path / "releases" / _RELEASE_ID / "venv" / "bin" / "python"
        )
        with pytest.raises(FrozenInstanceError):
            selected.release_id = "9" * 64
        assert events == [
            "enter:activation",
            "read:current:1",
            f"enter:release:{_RELEASE_ID}",
            "read:base_id",
            f"enter:base:{_BASE_ID}",
            "verify:base",
            "verify:release",
            "read:current:2",
        ]

    assert events[-3:] == [
        f"exit:base:{_BASE_ID}",
        f"exit:release:{_RELEASE_ID}",
        "exit:activation",
    ]


def test_snapshot_current_release_reuses_full_verification_and_releases_locks_before_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    held = {"activation": False, "release": False, "base": False}

    @contextmanager
    def activation(_root: Path, *, shared: bool):
        assert shared is True
        held["activation"] = True
        try:
            yield
        finally:
            held["activation"] = False

    @contextmanager
    def identity_lock(_root: Path, kind: str, _object_id: str, *, shared: bool):
        assert shared is True
        held[kind] = True
        try:
            yield
        finally:
            held[kind] = False

    def verify_base(_root: Path, _base_id: str) -> BaseMetadata:
        assert held == {"activation": True, "release": True, "base": True}
        return _base()

    def verify_release(
        _root: Path,
        _release_id: str,
        *,
        verified_base: BaseMetadata,
    ) -> ReleaseMetadata:
        assert verified_base == _base()
        assert held == {"activation": True, "release": True, "base": True}
        return _release()

    monkeypatch.setattr(binding, "_canonical_runtime_root", lambda root: Path(root))
    monkeypatch.setattr(binding, "_activation_lock", activation)
    monkeypatch.setattr(binding, "_id_lock", identity_lock)
    monkeypatch.setattr(binding, "_read_current_release_id", lambda _root: _RELEASE_ID)
    monkeypatch.setattr(
        binding,
        "_read_release_base_id_locked",
        lambda _root, _release_id: _BASE_ID,
    )
    monkeypatch.setattr(binding, "_verify_base_locked", verify_base)
    monkeypatch.setattr(binding, "_verify_release_locked", verify_release)

    selected = binding.snapshot_current_release(tmp_path)

    assert selected.release_id == _RELEASE_ID
    assert selected.runtime_revision == _REVISION
    assert held == {"activation": False, "release": False, "base": False}


def test_expected_release_mismatch_fails_before_id_locks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activation_held = False

    @contextmanager
    def activation(_root: Path, *, shared: bool):
        nonlocal activation_held
        assert shared is True
        activation_held = True
        try:
            yield
        finally:
            activation_held = False

    monkeypatch.setattr(binding, "_canonical_runtime_root", lambda root: Path(root))
    monkeypatch.setattr(binding, "_activation_lock", activation)
    monkeypatch.setattr(binding, "_read_current_release_id", lambda _root: "9" * 64)
    monkeypatch.setattr(
        binding,
        "_id_lock",
        lambda *_args, **_kwargs: pytest.fail("期望身份不一致时不能获取 ID 锁"),
    )

    with pytest.raises(binding.RuntimeReleaseBindingError, match="期望身份"):
        with binding.bind_current_release(tmp_path, _RELEASE_ID, _REVISION):
            pytest.fail("身份不一致时不能进入上下文")

    assert activation_held is False


def test_expected_revision_mismatch_fails_before_yield(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_verified_fakes(binding, monkeypatch, tmp_path)

    with pytest.raises(binding.RuntimeReleaseBindingError, match="期望版本"):
        with binding.bind_current_release(tmp_path, _RELEASE_ID, "b" * 40):
            pytest.fail("版本不一致时不能进入上下文")


@pytest.mark.parametrize(
    ("base", "release", "message"),
    [
        (replace(_base(), base_id="9" * 64), _release(), "基座锁定身份"),
        (_base(), replace(_release(), release_id="9" * 64), "release 锁定身份"),
        (_base(), replace(_release(), base_id="9" * 64), "release 锁定身份"),
    ],
)
def test_locked_metadata_drift_fails_before_yield(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    base: BaseMetadata,
    release: ReleaseMetadata,
    message: str,
) -> None:
    _install_verified_fakes(
        binding,
        monkeypatch,
        tmp_path,
        base=base,
        release=release,
    )

    with pytest.raises(binding.RuntimeReleaseBindingError, match=message):
        with binding.bind_current_release(tmp_path, _RELEASE_ID, _REVISION):
            pytest.fail("元数据漂移时不能进入上下文")


def test_current_drift_after_verification_fails_before_yield(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = iter((_RELEASE_ID, "9" * 64))
    _install_verified_fakes(
        binding,
        monkeypatch,
        tmp_path,
        read_current=lambda _root: next(current),
    )

    with pytest.raises(binding.RuntimeReleaseBindingError, match="绑定期间发生漂移"):
        with binding.bind_current_release(tmp_path, _RELEASE_ID, _REVISION):
            pytest.fail("current 漂移时不能进入上下文")
