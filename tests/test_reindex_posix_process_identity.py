from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from codev_platform.reindex.attempt_process import (
    validate_process_identity,
)
from codev_platform.reindex.posix_process import (
    LinuxProcessTable,
    encode_posix_native_ref,
    linux_birth_marker,
    parse_linux_stat,
)


pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Linux /proc 进程组后端只在 Linux/WSL 验证",
)


def test_linux_stat_parser_uses_rightmost_comm_delimiter() -> None:
    suffix = ["S", "1", "123", "123"] + ["0"] * 15 + ["987654"]
    raw = f"123 (name with ) delimiter) {' '.join(suffix)}\n".encode("ascii")

    info = parse_linux_stat(raw, expected_pid=123)

    assert info.pid == 123
    assert info.ppid == 1
    assert info.pgid == 123
    assert info.session_id == 123
    assert info.start_ticks == 987654


def test_linux_stat_parser_accepts_kernel_process_zero_group() -> None:
    suffix = ["S", "0", "0", "0"] + ["0"] * 15 + ["12"]
    raw = f"2 (kernel) {' '.join(suffix)}\n".encode("ascii")

    info = parse_linux_stat(raw, expected_pid=2)

    assert info.ppid == 0
    assert info.pgid == 0
    assert info.session_id == 0


def test_linux_identity_rejects_reused_pid_starttime() -> None:
    table = LinuxProcessTable()
    info = table.read(os.getpid())
    assert info is not None
    native_ref = encode_posix_native_ref(table.boot_id, info.pgid, info.session_id)
    identity = table.build_identity(info, native_ref)

    assert (
        validate_process_identity(
            identity,
            pid=info.pid,
            native_ref=native_ref,
            birth_marker=linux_birth_marker(table.boot_id, info.start_ticks),
        )
        == info.pid
    )
    with pytest.raises(ValueError, match="出生标记"):
        validate_process_identity(
            identity,
            pid=info.pid,
            native_ref=native_ref,
            birth_marker=linux_birth_marker(table.boot_id, info.start_ticks + 1),
        )


def test_linux_process_scan_stops_at_configured_bound(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    boot_path = tmp_path / "boot_id"
    boot_path.write_text(
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        encoding="ascii",
    )
    suffix = ["S", "0", "1", "1"] + ["0"] * 15 + ["12"]
    for pid in (11, 12):
        stat_path = proc_root / str(pid) / "stat"
        stat_path.parent.mkdir(parents=True)
        stat_path.write_text(
            f"{pid} (fixture) {' '.join(suffix)}\n",
            encoding="ascii",
        )
    table = LinuxProcessTable(
        proc_root=proc_root,
        boot_id_path=boot_path,
        max_processes=1,
    )

    scan = table.scan()

    assert scan.complete is False
    assert len(scan.processes) <= 1
