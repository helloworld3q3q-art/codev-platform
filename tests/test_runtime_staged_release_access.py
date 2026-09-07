from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform.core.runtime_models import (
    RUNTIME_ACCESS_PROFILE,
    BaseMetadata,
    ReleaseMetadata,
    RuntimeAbi,
)
from codev_platform import runtime_staged_release_access as staged_access
from codev_platform.runtime_service_process import ServiceAccount
from codev_platform.runtime_target_user_probe import RuntimeTargetUserProof


_BASE_ID = "a" * 64
_RELEASE_ID = "b" * 64
_CURRENT_RELEASE_ID = "c" * 64
_REVISION = "d" * 40
_SERVICE = ServiceAccount(
    name="worker",
    uid=23456,
    gid=23457,
    home=Path("/srv/worker"),
)


def _base(*, schema_version: int = 3) -> BaseMetadata:
    return BaseMetadata(
        schema_version=schema_version,
        access_profile=RUNTIME_ACCESS_PROFILE,
        base_id=_BASE_ID,
        requirements_sha256="e" * 64,
        approved_index_url="https://example.invalid/simple",
        artifact_manifest_sha256="f" * 64,
        freeze_sha256="1" * 64,
        purelib_inventory_sha256="2" * 64,
        abi=RuntimeAbi(
            implementation="cpython",
            python_version="3.12.4",
            cache_tag="cpython-312",
            soabi="cpython-312-x86_64-linux-gnu",
            platform_tag="linux-x86_64",
            machine="x86_64",
        ),
        created_at="2026-07-22T00:00:00Z",
        python_relative="venv/bin/python",
        purelib_relative="venv/lib/python3.12/site-packages",
        bin_relative="venv/bin",
        lock_relative="requirements.lock",
    )


def _release(*, schema_version: int = 1) -> ReleaseMetadata:
    return ReleaseMetadata(
        schema_version=schema_version,
        release_id=_RELEASE_ID,
        runtime_revision=_REVISION,
        wheel_sha256="3" * 64,
        base_id=_BASE_ID,
        base_requirements_sha256="e" * 64,
        base_metadata_sha256="4" * 64,
        app_freeze_sha256="5" * 64,
        created_at="2026-07-22T00:00:00Z",
        python_relative="venv/bin/python",
        purelib_relative="venv/lib/python3.12/site-packages",
        base_link_relative="base",
        base_pth_relative="venv/lib/python3.12/site-packages/codev_platform_base.pth",
    )


def _ports(
    events: list[str],
    *,
    current_release_id: str = _CURRENT_RELEASE_ID,
    base: BaseMetadata | None = None,
    release: ReleaseMetadata | None = None,
    probe: RuntimeTargetUserProof | None = None,
) -> SimpleNamespace:
    root = Path("/runtime")
    selected_base = _base() if base is None else base
    selected_release = _release() if release is None else release

    @contextmanager
    def activation_lock(value: Path, *, shared: bool):
        assert (value, shared) == (root, True)
        events.append("activation-enter")
        try:
            yield
        finally:
            events.append("activation-exit")

    @contextmanager
    def id_lock(value: Path, kind: str, object_id: str, *, shared: bool):
        assert value == root
        assert shared is False
        events.append(f"lock-enter:{kind}:{object_id}")
        try:
            yield
        finally:
            events.append(f"lock-exit:{kind}:{object_id}")

    def target_probe(
        value: Path,
        *,
        account: ServiceAccount,
        base_id: str,
        release_id: str,
    ) -> RuntimeTargetUserProof:
        assert (value, account, base_id, release_id) == (
            root,
            _SERVICE,
            _BASE_ID,
            _RELEASE_ID,
        )
        events.append("probe")
        if probe is not None:
            return probe
        return RuntimeTargetUserProof(
            access_profile=RUNTIME_ACCESS_PROFILE,
            service_uid=_SERVICE.uid,
            service_gid=_SERVICE.gid,
            base_id=_BASE_ID,
            release_id=_RELEASE_ID,
            runtime_revision=_REVISION,
            evidence_sha256="6" * 64,
        )

    return SimpleNamespace(
        require_runtime_root=lambda value: events.append("runtime-root") or Path(value),
        activation_lock=activation_lock,
        id_lock=id_lock,
        read_current=lambda value: events.append("read-current") or current_release_id,
        read_release_metadata=lambda value, release_id: (
            events.append("read-release") or selected_release
        ),
        verify_base_locked=lambda value, base_id: events.append("verify-base") or selected_base,
        verify_release_locked=lambda value, release_id, *, verified_base: (
            events.append("verify-release") or selected_release
        ),
        converge_namespace=lambda value, **_kwargs: events.append("namespace"),
        publish_objects=lambda value, **_kwargs: events.append("publish"),
        verify_service_access=lambda value, **_kwargs: events.append("verify-service"),
        probe_target_user=target_probe,
    )


def test_dry_run仅静态验证暂存版本且不发布访问投影(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(staged_access, "_default_ports", lambda: _ports(events))
    monkeypatch.setattr(sys, "dont_write_bytecode", False)

    proof = staged_access.publish_staged_release_service_access(
        Path("/runtime"),
        account=_SERVICE,
        release_id=_RELEASE_ID,
        dry_run=True,
    )

    assert proof.dry_run is True
    assert proof.target_user_evidence_sha256 is None
    assert sys.dont_write_bytecode is True
    assert "verify-base" in events
    assert "verify-release" in events
    assert not {"namespace", "publish", "verify-service", "probe"} & set(events)
    assert events[0] == "runtime-root"
    assert events[-1] == "activation-exit"


def test发布在静态复验后执行并以目标账号探针收口(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(staged_access, "_default_ports", lambda: _ports(events))

    proof = staged_access.publish_staged_release_service_access(
        Path("/runtime"),
        account=_SERVICE,
        release_id=_RELEASE_ID,
        dry_run=False,
    )

    assert proof.dry_run is False
    assert proof.target_user_evidence_sha256 == "6" * 64
    assert events.index("verify-release") < events.index("namespace")
    assert events.index(f"lock-exit:base:{_BASE_ID}") < events.index("namespace")
    assert events.index("namespace") < events.index("publish")
    assert events.index("publish") < events.index("verify-service")
    assert events.index("verify-service") < events.index("probe")
    assert events[-1] == "activation-exit"


def test拒绝把current误当作暂存版本发布(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        staged_access,
        "_default_ports",
        lambda: _ports(events, current_release_id=_RELEASE_ID),
    )

    with pytest.raises(staged_access.RuntimeStagedReleaseAccessError, match="不接受 current"):
        staged_access.publish_staged_release_service_access(
            Path("/runtime"),
            account=_SERVICE,
            release_id=_RELEASE_ID,
            dry_run=False,
        )

    assert "read-release" not in events
    assert not {"namespace", "publish", "verify-service", "probe"} & set(events)


def test静态对象不匹配时拒绝发布任何服务访问(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        staged_access,
        "_default_ports",
        lambda: _ports(events, base=object()),
    )

    with pytest.raises(staged_access.RuntimeStagedReleaseAccessError, match="静态对象"):
        staged_access.publish_staged_release_service_access(
            Path("/runtime"),
            account=_SERVICE,
            release_id=_RELEASE_ID,
            dry_run=False,
        )

    assert not {"namespace", "publish", "verify-service", "probe"} & set(events)
