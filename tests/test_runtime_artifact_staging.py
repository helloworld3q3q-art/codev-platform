"""wheelhouse 暂存续跑与原子发布测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from codev_platform.runtime_artifact_staging import (
    RuntimeArtifactStagingError,
    locked_artifact_publication,
    locked_artifact_workspace,
)


def test_success_publishes_incomplete_directory_atomically(tmp_path: Path) -> None:
    final = tmp_path / "wheelhouse"

    with locked_artifact_workspace(final) as workspace:
        assert workspace.working == tmp_path / ".wheelhouse.incomplete"
        (workspace.working / "demo.whl").write_bytes(b"demo")
        workspace.publish()

    assert (final / "demo.whl").read_bytes() == b"demo"
    assert not (tmp_path / ".wheelhouse.incomplete").exists()


def test_failure_keeps_incomplete_for_exact_resume(tmp_path: Path) -> None:
    final = tmp_path / "wheelhouse"

    with pytest.raises(RuntimeError, match="注入失败"):
        with locked_artifact_workspace(final) as workspace:
            (workspace.working / "partial.whl").write_bytes(b"partial")
            raise RuntimeError("注入失败")

    with locked_artifact_workspace(final) as resumed:
        assert (resumed.working / "partial.whl").read_bytes() == b"partial"


def test_final_and_incomplete_collision_fails_closed(tmp_path: Path) -> None:
    final = tmp_path / "wheelhouse"
    final.mkdir()
    (tmp_path / ".wheelhouse.incomplete").mkdir()

    with pytest.raises(RuntimeArtifactStagingError, match="同时存在"):
        with locked_artifact_workspace(final):
            pytest.fail("碰撞状态不得进入工作区")


def test_输出锁与wheelhouse锁使用独立身份且允许固定顺序嵌套(tmp_path: Path) -> None:
    output = tmp_path / "runtime.lock"
    wheelhouse = tmp_path / "wheelhouse"

    with locked_artifact_publication(output) as selected:
        assert selected == output
        with locked_artifact_workspace(wheelhouse) as workspace:
            (workspace.working / "demo.whl").write_bytes(b"demo")
            workspace.publish()

    assert (wheelhouse / "demo.whl").is_file()
