"""多个固定 systemd 条件安全共存的最终有效配置测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform.ops.reindex_codegraph_resume_managed_path import (
    RootOwnedRegularFileSnapshot,
)
from codev_platform.ops.systemd_condition_guard import (
    SystemdConditionGuardError,
    SystemdConditionSpec,
    verify_systemd_condition_guard,
)


_UNIT = "codev-mcp-codegraph.service"
_DEPLOYMENT = SystemdConditionSpec(
    Path("/etc/systemd/system/codev-mcp-codegraph.service.d/10-deployment.conf"),
    b"[Unit]\nConditionPathExists=!/var/lib/codev-platform/runtime/deployment.guard\n",
)
_CODEGRAPH = SystemdConditionSpec(
    Path("/etc/systemd/system/codev-mcp-codegraph.service.d/99-codegraph.conf"),
    b"[Unit]\nConditionPathExists=!/var/lib/codev-platform/codegraph.guard\n",
)


def _snapshot(content: bytes, *, mode: int = 0o644):
    return RootOwnedRegularFileSnapshot(content, mode, 0, 0)


def _runner(paths: tuple[Path, ...]):
    output = "DropInPaths=" + " ".join(path.as_posix() for path in paths) + "\n"
    return lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=output)


def test_要求条件与已知可选条件可以安全共存() -> None:
    snapshots = {
        _DEPLOYMENT.path: _snapshot(_DEPLOYMENT.content),
        _CODEGRAPH.path: _snapshot(_CODEGRAPH.content),
    }

    verify_systemd_condition_guard(
        _UNIT,
        required=_DEPLOYMENT,
        allowed=(_CODEGRAPH,),
        platform_name="linux",
        reader=lambda path, **_kwargs: snapshots.get(path),
        command_runner=_runner((_DEPLOYMENT.path, _CODEGRAPH.path)),
    )


@pytest.mark.parametrize(
    "content",
    (
        b"[Unit]\nConditionPathExists=\n",
        b"[Unit]\nAssertPathExists=/tmp/unknown\n",
        b"[Unit]\\\n\nConditionPathExists=\n",
    ),
)
def test_未知dropin不得重置或增加condition_assert(content: bytes) -> None:
    unknown = Path("/etc/systemd/system/codev-mcp-codegraph.service.d/zz-unknown.conf")
    snapshots = {
        _DEPLOYMENT.path: _snapshot(_DEPLOYMENT.content),
        unknown: _snapshot(content),
    }

    with pytest.raises(SystemdConditionGuardError, match="未知条件"):
        verify_systemd_condition_guard(
            _UNIT,
            required=_DEPLOYMENT,
            platform_name="linux",
            reader=lambda path, **_kwargs: snapshots.get(path),
            command_runner=_runner((_DEPLOYMENT.path, unknown)),
        )


def test_磁盘文件存在但systemd未加载时失败关闭() -> None:
    with pytest.raises(SystemdConditionGuardError, match="未进入有效解析"):
        verify_systemd_condition_guard(
            _UNIT,
            required=_DEPLOYMENT,
            platform_name="linux",
            reader=lambda _path, **_kwargs: _snapshot(_DEPLOYMENT.content),
            command_runner=_runner(()),
        )


def test_已知条件路径存在但内容漂移时失败关闭() -> None:
    snapshots = {
        _DEPLOYMENT.path: _snapshot(_DEPLOYMENT.content),
        _CODEGRAPH.path: _snapshot(b"[Unit]\nConditionPathExists=\n"),
    }

    with pytest.raises(SystemdConditionGuardError, match="固定条件不受信任"):
        verify_systemd_condition_guard(
            _UNIT,
            required=_DEPLOYMENT,
            allowed=(_CODEGRAPH,),
            platform_name="linux",
            reader=lambda path, **_kwargs: snapshots.get(path),
            command_runner=_runner((_DEPLOYMENT.path, _CODEGRAPH.path)),
        )


def test_已知条件仅换成CRLF仍按原像漂移拒绝() -> None:
    snapshots = {
        _DEPLOYMENT.path: _snapshot(_DEPLOYMENT.content),
        _CODEGRAPH.path: _snapshot(_CODEGRAPH.content.replace(b"\n", b"\r\n")),
    }

    with pytest.raises(SystemdConditionGuardError, match="固定条件不受信任"):
        verify_systemd_condition_guard(
            _UNIT,
            required=_DEPLOYMENT,
            allowed=(_CODEGRAPH,),
            platform_name="linux",
            reader=lambda path, **_kwargs: snapshots.get(path),
            command_runner=_runner((_DEPLOYMENT.path, _CODEGRAPH.path)),
        )


def test_未知但不含条件的服务配置不会产生误拒绝() -> None:
    service = Path("/etc/systemd/system/codev-mcp-codegraph.service.d/90-release.conf")
    snapshots = {
        _DEPLOYMENT.path: _snapshot(_DEPLOYMENT.content),
        service: _snapshot(b"[Service]\nEnvironment=SAFE=1\n"),
    }

    verify_systemd_condition_guard(
        _UNIT,
        required=_DEPLOYMENT,
        platform_name="linux",
        reader=lambda path, **_kwargs: snapshots.get(path),
        command_runner=_runner((_DEPLOYMENT.path, service)),
    )


def test_未知CRLF服务配置不会误拒绝() -> None:
    service = Path("/etc/systemd/system/codev-mcp-codegraph.service.d/20-cpu.conf")
    snapshots = {
        _DEPLOYMENT.path: _snapshot(_DEPLOYMENT.content),
        service: _snapshot(b"[Service]\r\nEnvironment=SAFE=1\r\n"),
    }

    verify_systemd_condition_guard(
        _UNIT,
        required=_DEPLOYMENT,
        platform_name="linux",
        reader=lambda path, **_kwargs: snapshots.get(path),
        command_runner=_runner((_DEPLOYMENT.path, service)),
    )
