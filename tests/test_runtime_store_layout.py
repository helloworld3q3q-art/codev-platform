"""运行时 store 固定布局、窄协议与策略边界测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from codev_platform.runtime_store_protocols import RuntimeStorePolicy, StoredSnapshot
from codev_platform.runtime_storage import (
    RuntimeStoragePathError,
    attempt_terminal_evidence_path,
    control_lease_history_path,
    rollback_bundle_pending_path,
    serving_fence_record_path,
    serving_permit_stage_path,
    serving_permit_record_path,
)


_ATTEMPT_ID = "a" * 32
_RECORD_SHA256 = "b" * 64


def _directory_symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"当前测试环境不能创建符号链接：{error}")


def test运行时布局为终态证据与完整lease_lineage提供固定地址(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"

    assert attempt_terminal_evidence_path(root, _ATTEMPT_ID) == (
        root / "attempts" / _ATTEMPT_ID / "terminal-evidence.json"
    )
    assert control_lease_history_path(root, _ATTEMPT_ID, _RECORD_SHA256) == (
        root / "control-leases" / _ATTEMPT_ID / f"{_RECORD_SHA256}.json"
    )
    assert serving_fence_record_path(root, _ATTEMPT_ID, _RECORD_SHA256) == (
        root / "serving-fences" / _ATTEMPT_ID / f"{_RECORD_SHA256}.json"
    )
    assert serving_permit_record_path(root, _ATTEMPT_ID, _RECORD_SHA256) == (
        root / "serving-permits" / _ATTEMPT_ID / f"{_RECORD_SHA256}.json"
    )
    assert serving_permit_stage_path(root, _ATTEMPT_ID, _RECORD_SHA256) == (
        root / "serving-permit-stages" / _ATTEMPT_ID / f"{_RECORD_SHA256}.json"
    )
    assert rollback_bundle_pending_path(root, _ATTEMPT_ID) == (
        root / "rollback-bundles" / _ATTEMPT_ID / "bundle.pending.json"
    )


def test新布局独立拒绝attempt摘要混用与路径逃逸(tmp_path: Path) -> None:
    root = tmp_path / "runtime"

    with pytest.raises(RuntimeStoragePathError):
        attempt_terminal_evidence_path(root, _RECORD_SHA256)
    with pytest.raises(RuntimeStoragePathError):
        control_lease_history_path(root, _ATTEMPT_ID, _ATTEMPT_ID)
    with pytest.raises(RuntimeStoragePathError):
        serving_fence_record_path(root, "../escape", _RECORD_SHA256)
    with pytest.raises(RuntimeStoragePathError):
        serving_permit_record_path(root, _ATTEMPT_ID, "0" * 64)
    with pytest.raises(RuntimeStoragePathError):
        serving_permit_stage_path(root, _ATTEMPT_ID, "0" * 64)
    with pytest.raises(RuntimeStoragePathError):
        rollback_bundle_pending_path(root, "0" * 64)


def test新布局拒绝符号链接运行时根(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_root = tmp_path / "linked-runtime"
    _directory_symlink(linked_root, outside)

    with pytest.raises(RuntimeStoragePathError, match="符号链接"):
        control_lease_history_path(linked_root, _ATTEMPT_ID, _RECORD_SHA256)


def test存储策略拒绝相对根与负属主并保留可信配置(tmp_path: Path) -> None:
    policy = RuntimeStorePolicy(root=tmp_path / "runtime", owner_uid=0)

    assert policy.root == tmp_path / "runtime"
    assert policy.owner_uid == 0

    with pytest.raises(ValueError, match="绝对路径"):
        RuntimeStorePolicy(root=Path("relative-runtime"), owner_uid=0)
    with pytest.raises(ValueError, match="owner_uid"):
        RuntimeStorePolicy(root=tmp_path / "runtime", owner_uid=-1)


def test存储策略拒绝已存在符号链接运行时根(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_root = tmp_path / "linked-runtime"
    _directory_symlink(linked_root, outside)

    with pytest.raises(ValueError, match="符号链接"):
        RuntimeStorePolicy(root=linked_root, owner_uid=0)


def test快照拒绝错误摘要并保留泛型值() -> None:
    snapshot = StoredSnapshot(value={"status": "active"}, sha256=_RECORD_SHA256)

    assert snapshot.value == {"status": "active"}
    assert snapshot.sha256 == _RECORD_SHA256

    with pytest.raises(ValueError, match="sha256"):
        StoredSnapshot(value="bad", sha256="not-a-sha")
