"""CodeGraph 耐久维护门禁的可信文件契约测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _snapshot(module, *, content: bytes | None = None, mode: int = 0o644):
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        RootOwnedRegularFileSnapshot,
    )

    return RootOwnedRegularFileSnapshot(
        module.CODEGRAPH_MAINTENANCE_HOLD_CONTENT if content is None else content,
        mode,
        0,
        0,
    )


def test_启用门禁必须先耐久写入再按可信原像复证() -> None:
    from codev_platform.ops import reindex_codegraph_maintenance_hold as module

    events: list[object] = []

    def write(path, content, *, mode, uid, gid) -> None:
        events.append(("write", path, content, mode, uid, gid))

    module.activate_codegraph_maintenance_hold(
        writer=write,
        reader=lambda path, **_kwargs: events.append(("read", path)) or _snapshot(module),
    )

    assert events == [
        (
            "write",
            module.CODEGRAPH_MAINTENANCE_HOLD_PATH,
            module.CODEGRAPH_MAINTENANCE_HOLD_CONTENT,
            0o644,
            0,
            0,
        ),
        ("read", module.CODEGRAPH_MAINTENANCE_HOLD_PATH),
    ]


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        "wrong-content",
        "wrong-mode",
        "wrong-owner",
    ],
)
def test_门禁证明拒绝缺失或不精确原像(snapshot: str | None) -> None:
    from codev_platform.ops import reindex_codegraph_maintenance_hold as module

    value = (
        None
        if snapshot is None
        else _snapshot(module, content=b"wrong\n")
        if snapshot == "wrong-content"
        else _snapshot(module, mode=0o600)
        if snapshot == "wrong-mode"
        else SimpleNamespace(
            content=module.CODEGRAPH_MAINTENANCE_HOLD_CONTENT,
            mode=0o644,
            uid=1,
            gid=0,
        )
    )

    with pytest.raises(module.CodegraphMaintenanceHoldError, match="不受信任"):
        module.verify_codegraph_maintenance_hold(
            reader=lambda _path, **_kwargs: value,
        )


def test_解除门禁必须先证明原像并在删除后证明缺失() -> None:
    from codev_platform.ops import reindex_codegraph_maintenance_hold as module

    present = True
    events: list[str] = []

    def read(_path, **_kwargs):
        events.append("read")
        return _snapshot(module) if present else None

    def remove(_path) -> bool:
        nonlocal present
        events.append("remove")
        present = False
        return True

    module.deactivate_codegraph_maintenance_hold(reader=read, remover=remove)

    assert events == ["read", "remove", "read"]


def test_解除门禁遇到删除器伪成功时失败关闭() -> None:
    from codev_platform.ops import reindex_codegraph_maintenance_hold as module

    with pytest.raises(module.CodegraphMaintenanceHoldError, match="无法解除"):
        module.deactivate_codegraph_maintenance_hold(
            reader=lambda _path, **_kwargs: _snapshot(module),
            remover=lambda _path: False,
        )
