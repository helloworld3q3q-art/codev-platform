"""systemd 环境文件的保留变量与格式边界测试。"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform.core.systemd_environment_file import (
    SystemdEnvironmentFileError,
    parse_systemd_environment_keys,
    validate_systemd_environment_file,
)


def test_business_environment_keys_are_preserved_without_reading_values() -> None:
    content = (
        b"# comment\n"
        b"CODEGRAPH_CMD='/opt/codegraph/bin/server --stdio'\n"
        b"CODEV_PLATFORM_MEMORY_DSN=postgresql://secret\n"
        b"HTTPS_PROXY=http://proxy.example\n"
    )

    assert parse_systemd_environment_keys(content) == {
        "CODEGRAPH_CMD",
        "CODEV_PLATFORM_MEMORY_DSN",
        "HTTPS_PROXY",
    }


@pytest.mark.parametrize(
    "key",
    (
        "PATH",
        "PYTHONPATH",
        "PYTHONINSPECT",
        "CODEV_PLATFORM_RELEASE_FILE",
        "LD_PRELOAD",
        "HOME",
        "INVOCATION_ID",
        "NOTIFY_SOCKET",
    ),
)
def test_runtime_or_loader_environment_override_is_rejected(key: str) -> None:
    with pytest.raises(SystemdEnvironmentFileError, match="保留变量"):
        parse_systemd_environment_keys(f"{key}=unsafe\n".encode())


@pytest.mark.parametrize(
    "content",
    (b"BROKEN\n", b"A=1\nA=2\n", b"A=first\\\nsecond\n", b"A=\xff\n"),
)
def test_ambiguous_or_invalid_environment_syntax_fails_closed(content: bytes) -> None:
    with pytest.raises(SystemdEnvironmentFileError):
        parse_systemd_environment_keys(content)


@pytest.mark.skipif(os.name != "posix", reason="root 所有权和 POSIX 权限仅在 Linux 验证")
def test_environment_file_rejects_replaceable_ancestor_directory(tmp_path: Path) -> None:
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o777)
    unsafe.chmod(0o777)
    environment = unsafe / "platform.env"
    environment.write_bytes(b"SAFE=value\n")

    with pytest.raises(SystemdEnvironmentFileError, match="目录"):
        validate_systemd_environment_file(environment)


def test_environment_file_snapshot_rejects_metadata_drift() -> None:
    from codev_platform.core import systemd_environment_file as module

    stable = SimpleNamespace(
        st_mode=0o100600,
        st_uid=0,
        st_gid=0,
        st_nlink=1,
        st_size=11,
        st_dev=1,
        st_ino=2,
        st_mtime_ns=3,
    )
    changed = SimpleNamespace(**{**vars(stable), "st_mtime_ns": 4})

    assert module._same_environment_file(stable, stable)
    assert not module._same_environment_file(stable, changed)
