from __future__ import annotations

import sys
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform import _runtime_fd_tree_snapshot as snapshots
from codev_platform import runtime_current_access_repair as repair
from codev_platform.core.runtime_models import (
    RUNTIME_ACCESS_PROFILE,
    BaseMetadata,
    ReleaseMetadata,
    RuntimeAbi,
)
from codev_platform.runtime_fd_tree import RuntimeFdTreeReport
from codev_platform.runtime_service_process import ServiceAccount
from codev_platform.runtime_target_user_probe import RuntimeTargetUserProof


_BASE_ID = "a" * 64
_RELEASE_ID = "b" * 64
_OTHER_RELEASE_ID = "c" * 64
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


def _report(seed: bytes, *, entries: int, total_bytes: int) -> RuntimeFdTreeReport:
    identity = snapshots.new_identity_snapshot(
        identity_digest=seed * 32,
        entries=entries,
        total_bytes=total_bytes,
        root_device=7,
        root_inode=11,
    )
    access = snapshots.new_access_repair_snapshot(
        identity_digest=seed * 32,
        entries=entries,
        total_bytes=total_bytes,
        root_device=7,
        root_inode=11,
    )
    return RuntimeFdTreeReport(
        entries=entries,
        total_bytes=total_bytes,
        root_device=7,
        root_inode=11,
        identity_snapshot=identity,
        access_repair_snapshot=access,
    )


def _ports(
    events: list[str],
    *,
    current_ids: tuple[str, ...] = (_RELEASE_ID,),
    base: BaseMetadata | None = None,
    release: ReleaseMetadata | None = None,
    release_virtual_error: bool = False,
) -> SimpleNamespace:
    root = Path("/runtime")
    current = deque(current_ids)
    selected_base = _base() if base is None else base
    selected_release = _release() if release is None else release
    reports = {
        root / "bases" / _BASE_ID: _report(b"a", entries=21, total_bytes=34),
        root / "releases" / _RELEASE_ID: _report(b"b", entries=13, total_bytes=55),
    }

    @contextmanager
    def activation_lock(value: Path, *, shared: bool):
        assert value == root
        assert shared is True
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

    def read_current(value: Path) -> str:
        assert value == root
        events.append("read-current")
        return current[0] if len(current) == 1 else current.popleft()

    def read_release_metadata(value: Path, release_id: str) -> ReleaseMetadata:
        assert value == root
        assert release_id == _RELEASE_ID
        events.append("read-release")
        return selected_release

    def verify_base_tolerant(
        value: Path,
        base_id: str,
        *,
        prune_generated_bytecode: bool,
    ) -> BaseMetadata:
        assert value == root
        assert base_id == _BASE_ID
        events.append(f"verify-base-tolerant:{prune_generated_bytecode}")
        return selected_base

    def verify_release_tolerant(
        value: Path,
        release_id: str,
        *,
        verified_base: BaseMetadata,
        prune_generated_bytecode: bool,
    ) -> ReleaseMetadata:
        assert value == root
        assert release_id == _RELEASE_ID
        assert verified_base is selected_base
        events.append(f"verify-release-tolerant:{prune_generated_bytecode}")
        if not prune_generated_bytecode and release_virtual_error:
            raise repair.RuntimeCurrentAccessRepairError("模拟 release 虚拟预检失败")
        return selected_release

    def preflight(value: Path) -> RuntimeFdTreeReport:
        events.append(f"preflight:{value.name}")
        return reports[value]

    def converge(value: Path, snapshot: object) -> RuntimeFdTreeReport:
        assert snapshot is reports[value].access_repair_snapshot
        events.append(f"converge:{value.name}")
        return reports[value]

    def verify_base_strict(value: Path, base_id: str) -> BaseMetadata:
        assert value == root
        assert base_id == _BASE_ID
        events.append("verify-base-strict")
        return selected_base

    def verify_release_strict(
        value: Path,
        release_id: str,
        *,
        verified_base: BaseMetadata,
    ) -> ReleaseMetadata:
        assert value == root
        assert release_id == _RELEASE_ID
        assert verified_base is selected_base
        events.append("verify-release-strict")
        return selected_release

    def probe(value: Path, *, account: ServiceAccount, base_id: str, release_id: str):
        assert (value, account, base_id, release_id) == (root, _SERVICE, _BASE_ID, _RELEASE_ID)
        events.append("probe")
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
        read_current=read_current,
        read_release_metadata=read_release_metadata,
        verify_base_tolerant=verify_base_tolerant,
        verify_release_tolerant=verify_release_tolerant,
        preflight_completed_access=preflight,
        converge_completed_access=converge,
        verify_base_strict=verify_base_strict,
        verify_release_strict=verify_release_strict,
        converge_namespace=lambda value, **_kwargs: events.append("namespace"),
        publish_objects=lambda value, **_kwargs: events.append("publish"),
        verify_service_access=lambda value, **_kwargs: events.append("verify-service"),
        probe_target_user=probe,
    )


def test_dry_run完整验证后零写入且不触发服务探针(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(repair, "_default_ports", lambda: _ports(events))
    monkeypatch.setattr(sys, "dont_write_bytecode", False)

    proof = repair.repair_current_runtime_service_access(
        Path("/runtime"),
        account=_SERVICE,
        dry_run=True,
    )

    assert proof.dry_run is True
    assert (proof.base_entries, proof.base_total_bytes) == (21, 34)
    assert (proof.release_entries, proof.release_total_bytes) == (13, 55)
    assert proof.target_user_evidence_sha256 is None
    assert sys.dont_write_bytecode is True
    assert "verify-base-tolerant:False" in events
    assert "verify-release-tolerant:False" in events
    assert "verify-base-tolerant:True" not in events
    assert "verify-release-tolerant:True" not in events
    assert f"converge:{_RELEASE_ID}" not in events
    assert f"converge:{_BASE_ID}" not in events
    assert not {"namespace", "publish", "verify-service", "probe"} & set(events)
    assert events[0] == "runtime-root"
    assert events[1] == "activation-enter"
    assert events[-1] == "activation-exit"
    assert events.count("read-current") == 2


def test真实修复先收敛再严格复验最后发布服务访问(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(repair, "_default_ports", lambda: _ports(events))

    proof = repair.repair_current_runtime_service_access(
        Path("/runtime"),
        account=_SERVICE,
        dry_run=False,
    )

    assert proof.dry_run is False
    assert proof.target_user_evidence_sha256 == "6" * 64
    assert "verify-base-tolerant:True" in events
    assert "verify-release-tolerant:True" in events
    assert events.index("verify-base-tolerant:False") < events.index(
        "verify-release-tolerant:False"
    )
    assert events.index("verify-release-tolerant:False") < events.index("verify-base-tolerant:True")
    assert events.index("verify-base-tolerant:True") < events.index("verify-release-tolerant:True")
    assert events.index("verify-release-tolerant:True") < events.index(f"converge:{_BASE_ID}")
    assert events.index(f"converge:{_BASE_ID}") < events.index("verify-base-strict")
    assert events.index(f"converge:{_RELEASE_ID}") < events.index("verify-release-strict")
    assert events.index("verify-release-strict") < events.index("namespace")
    assert events.index("namespace") < events.index("publish")
    assert events.index("publish") < events.index("verify-service")
    assert events.index("verify-service") < events.index("probe")
    assert events.index("lock-exit:base:" + _BASE_ID) < events.index("namespace")
    assert events[-1] == "activation-exit"


def test真实修复向命名空间仅传服务身份(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    ports = _ports(events)

    def converge_namespace(
        value: Path,
        *,
        service_uid: int,
        service_gid: int,
    ) -> None:
        assert (value, service_uid, service_gid) == (
            Path("/runtime"),
            _SERVICE.uid,
            _SERVICE.gid,
        )
        events.append("namespace")

    ports.converge_namespace = converge_namespace
    monkeypatch.setattr(repair, "_default_ports", lambda: ports)

    proof = repair.repair_current_runtime_service_access(
        Path("/runtime"),
        account=_SERVICE,
        dry_run=False,
    )

    assert proof.dry_run is False
    assert events.index("namespace") < events.index("publish")


def test拒绝非schema3当前对象且不做任何收敛或发布(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        repair,
        "_default_ports",
        lambda: _ports(events, base=_base(schema_version=2)),
    )

    with pytest.raises(repair.RuntimeCurrentAccessRepairError):
        repair.repair_current_runtime_service_access(
            Path("/runtime"),
            account=_SERVICE,
            dry_run=False,
        )

    assert f"converge:{_BASE_ID}" not in events
    assert not {"namespace", "publish", "verify-service", "probe"} & set(events)


def testrelease虚拟预检失败时不清理任一对象或发布服务访问(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        repair,
        "_default_ports",
        lambda: _ports(events, release_virtual_error=True),
    )

    with pytest.raises(repair.RuntimeCurrentAccessRepairError):
        repair.repair_current_runtime_service_access(
            Path("/runtime"),
            account=_SERVICE,
            dry_run=False,
        )

    assert "verify-base-tolerant:False" in events
    assert "verify-release-tolerant:False" in events
    assert "verify-base-tolerant:True" not in events
    assert "verify-release-tolerant:True" not in events
    assert not any(item.startswith("converge:") for item in events)
    assert not {"namespace", "publish", "verify-service", "probe"} & set(events)


def testcurrent在对象修复后漂移时不发布服务访问(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        repair,
        "_default_ports",
        lambda: _ports(events, current_ids=(_RELEASE_ID, _OTHER_RELEASE_ID)),
    )

    with pytest.raises(repair.RuntimeCurrentAccessRepairError):
        repair.repair_current_runtime_service_access(
            Path("/runtime"),
            account=_SERVICE,
            dry_run=False,
        )

    assert f"converge:{_BASE_ID}" in events
    assert not {"namespace", "publish", "verify-service", "probe"} & set(events)
