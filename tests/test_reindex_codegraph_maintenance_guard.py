"""CodeGraph 永久维护条件 guard 的可信文件与有效解析测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def _snapshot(content: bytes, *, mode: int = 0o644):
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        RootOwnedRegularFileSnapshot,
    )

    return RootOwnedRegularFileSnapshot(content, mode, 0, 0)


def _ok(*, stdout: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="不得泄露原始错误")


def test_安装永久guard必须先落盘reload再枚举全部有效dropin() -> None:
    from codev_platform.ops import reindex_codegraph_maintenance_guard as module

    legacy = Path("/etc/systemd/system/codev-mcp-codegraph.service.d/90-codev-release.conf")
    snapshots = {legacy: _snapshot(b"[Service]\nEnvironment=SAFE=1\n")}
    events: list[object] = []

    def write(path: Path, content: bytes, *, mode: int, uid: int, gid: int) -> None:
        events.append(("write", path, content, mode, uid, gid))
        snapshots[path] = _snapshot(content, mode=mode)

    def read(path: Path, **_kwargs):
        events.append(("read", path))
        return snapshots.get(path)

    def run(command: tuple[str, ...], **_kwargs) -> SimpleNamespace:
        events.append(command)
        if command == (
            "systemctl",
            "show",
            "codev-mcp-codegraph.service",
            "--property=DropInPaths",
        ):
            return _ok(
                stdout=(
                    f"DropInPaths={legacy.as_posix()} "
                    f"{module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH.as_posix()}\n"
                )
            )
        return _ok()

    module.ensure_codegraph_maintenance_guard(
        platform_name="linux",
        writer=write,
        reader=read,
        command_runner=run,
    )

    assert events == [
        (
            "write",
            module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH,
            module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_CONTENT,
            0o644,
            0,
            0,
        ),
        ("read", module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH),
        ("systemctl", "daemon-reload"),
        (
            "systemctl",
            "show",
            "codev-mcp-codegraph.service",
            "--property=DropInPaths",
        ),
        ("read", legacy),
        ("read", module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH),
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
def test_guard证明拒绝缺失或不精确原像(snapshot: str | None) -> None:
    from codev_platform.ops import reindex_codegraph_maintenance_guard as module

    value = (
        None
        if snapshot is None
        else _snapshot(b"[Unit]\nConditionPathExists=/wrong\n")
        if snapshot == "wrong-content"
        else _snapshot(module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_CONTENT, mode=0o600)
        if snapshot == "wrong-mode"
        else SimpleNamespace(
            content=module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_CONTENT,
            mode=0o644,
            uid=1,
            gid=0,
        )
    )

    with pytest.raises(module.CodegraphMaintenanceGuardError, match="不受信任"):
        module.verify_codegraph_maintenance_guard(
            platform_name="linux",
            reader=lambda _path, **_kwargs: value,
            command_runner=lambda *_args, **_kwargs: _ok(
                stdout=(
                    f"DropInPaths={module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH.as_posix()}\n"
                )
            ),
        )


@pytest.mark.parametrize(
    "content",
    [
        b"[Unit]\nConditionPathExists=\n",
        b"[Unit]\nAssertPathExists=/tmp/unsafe\n",
        b"[Unit]\nConditionPathExists=|/tmp/unsafe\n",
        b"[Unit]\\\n\nConditionPathExists=\n",
    ],
)
def test_guard证明拒绝其他dropin中的condition_assert或续行(content: bytes) -> None:
    from codev_platform.ops import reindex_codegraph_maintenance_guard as module

    unknown = Path("/etc/systemd/system/codev-mcp-codegraph.service.d/98-unknown.conf")
    snapshots = {
        unknown: _snapshot(content),
        module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH: _snapshot(
            module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_CONTENT
        ),
    }

    with pytest.raises(module.CodegraphMaintenanceGuardError, match="条件"):
        module.verify_codegraph_maintenance_guard(
            platform_name="linux",
            reader=lambda path, **_kwargs: snapshots.get(path),
            command_runner=lambda *_args, **_kwargs: _ok(
                stdout=(
                    f"DropInPaths={unknown.as_posix()} "
                    f"{module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH.as_posix()}\n"
                )
            ),
        )


def test_guard必须是有效dropin最后一项() -> None:
    from codev_platform.ops import reindex_codegraph_maintenance_guard as module

    later = Path("/etc/systemd/system/codev-mcp-codegraph.service.d/zz-unknown.conf")
    snapshots = {
        module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH: _snapshot(
            module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_CONTENT
        ),
        later: _snapshot(b"[Service]\nEnvironment=SAFE=1\n"),
    }

    with pytest.raises(module.CodegraphMaintenanceGuardError, match="最后"):
        module.verify_codegraph_maintenance_guard(
            platform_name="linux",
            reader=lambda path, **_kwargs: snapshots.get(path),
            command_runner=lambda *_args, **_kwargs: _ok(
                stdout=(
                    "DropInPaths="
                    f"{module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH.as_posix()} "
                    f"{later.as_posix()}\n"
                )
            ),
        )


def test_guard证明拒绝systemd未加载永久文件() -> None:
    from codev_platform.ops import reindex_codegraph_maintenance_guard as module

    with pytest.raises(module.CodegraphMaintenanceGuardError, match="有效解析"):
        module.verify_codegraph_maintenance_guard(
            platform_name="linux",
            reader=lambda path, **_kwargs: (
                _snapshot(module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_CONTENT)
                if path == module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH
                else None
            ),
            command_runner=lambda *_args, **_kwargs: _ok(
                stdout="DropInPaths=/etc/systemd/system/other.conf\n"
            ),
        )


def test_guard证明拒绝可信读取器发现的符号链接() -> None:
    from codev_platform.ops import reindex_codegraph_maintenance_guard as module
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        TrustedManagedPathError,
    )

    def reject_symlink(_path: Path, **_kwargs):
        raise TrustedManagedPathError("符号链接不得跟随")

    with pytest.raises(module.CodegraphMaintenanceGuardError, match="不受信任"):
        module.verify_codegraph_maintenance_guard(
            platform_name="linux",
            reader=reject_symlink,
            command_runner=lambda *_args, **_kwargs: _ok(
                stdout=(
                    f"DropInPaths={module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH.as_posix()}\n"
                )
            ),
        )


def test_CodeGraph维护条件允许固定部署总门禁在其之前共存() -> None:
    from codev_platform.ops import reindex_codegraph_maintenance_guard as module
    from codev_platform.runtime_systemd_gate_contract import (
        DEPLOYMENT_GUARD_DROP_IN_CONTENT,
        deployment_guard_drop_in_path,
    )

    deployment = Path(deployment_guard_drop_in_path("codev-mcp-codegraph.service").as_posix())
    snapshots = {
        deployment: _snapshot(DEPLOYMENT_GUARD_DROP_IN_CONTENT),
        module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH: _snapshot(
            module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_CONTENT
        ),
    }

    module.verify_codegraph_maintenance_guard(
        platform_name="linux",
        reader=lambda path, **_kwargs: snapshots.get(path),
        command_runner=lambda *_args, **_kwargs: _ok(
            stdout=(
                f"DropInPaths={deployment.as_posix()} "
                f"{module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH.as_posix()}\n"
            )
        ),
    )


def test_CodeGraph维护条件接受无条件的CRLF旧dropin() -> None:
    from codev_platform.ops import reindex_codegraph_maintenance_guard as module

    legacy = Path("/etc/systemd/system/codev-mcp-codegraph.service.d/20-cpu.conf")
    snapshots = {
        legacy: _snapshot(b"[Service]\r\nEnvironment=SAFE=1\r\n"),
        module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH: _snapshot(
            module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_CONTENT
        ),
    }

    module.verify_codegraph_maintenance_guard(
        platform_name="linux",
        reader=lambda path, **_kwargs: snapshots.get(path),
        command_runner=lambda *_args, **_kwargs: _ok(
            stdout=(
                f"DropInPaths={legacy.as_posix()} "
                f"{module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH.as_posix()}\n"
            )
        ),
    )
