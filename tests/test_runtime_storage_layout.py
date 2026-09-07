"""运行代际 store 的固定路径与独立身份校验测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from codev_platform.runtime_storage import (
    RuntimeStoragePathError,
    acceptance_record_path,
    active_recovery_envelope_path,
    attempt_journal_path,
    attempt_record_path,
    attempt_recovery_envelope_path,
    attempt_reservation_path,
    control_lease_record_path,
    generation_record_path,
    generation_state_path,
    rollback_bundle_path,
)


_GENERATION_ID = "a" * 64
_ATTEMPT_ID = "b" * 32


def test_generation与attempt使用独立验证器生成固定路径(tmp_path: Path) -> None:
    root = tmp_path / "runtime"

    assert generation_record_path(root, _GENERATION_ID) == (
        root / "generations" / _GENERATION_ID / "generation.json"
    )
    assert attempt_reservation_path(root, _ATTEMPT_ID) == (
        root / "attempts" / _ATTEMPT_ID / "reservation.json"
    )
    assert attempt_record_path(root, _ATTEMPT_ID).name == "attempt.json"
    assert attempt_journal_path(root, _ATTEMPT_ID).name == "journal.json"
    assert attempt_recovery_envelope_path(root, _ATTEMPT_ID).name == ("recovery-envelope.json")
    assert rollback_bundle_path(root, _ATTEMPT_ID) == (
        root / "rollback-bundles" / _ATTEMPT_ID / "bundle.json"
    )
    assert acceptance_record_path(root, _ATTEMPT_ID) == (
        root / "acceptances" / f"{_ATTEMPT_ID}.json"
    )


@pytest.mark.parametrize(
    ("builder", "object_id"),
    (
        (generation_record_path, _ATTEMPT_ID),
        (generation_record_path, "0" * 64),
        (generation_record_path, "A" * 64),
        (attempt_reservation_path, _GENERATION_ID),
        (attempt_reservation_path, "0" * 32),
        (attempt_reservation_path, "../escape"),
    ),
)
def test_generation与attempt身份不能混用或逃逸(
    tmp_path: Path,
    builder,
    object_id: str,
) -> None:
    with pytest.raises(RuntimeStoragePathError):
        builder(tmp_path / "runtime", object_id)


def test全局文件名固定且不接受调用方路径片段(tmp_path: Path) -> None:
    root = tmp_path / "runtime"

    assert generation_state_path(root) == root / "generation-state.json"
    assert active_recovery_envelope_path(root) == (root / "active-recovery-envelope.json")
    assert control_lease_record_path(root) == root / "control-lease.json"
