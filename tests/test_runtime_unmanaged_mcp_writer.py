"""部署停写对 systemd 外 MCP 守护进程的缺失证明。"""

from __future__ import annotations

from pathlib import Path

import pytest


def _stat(pid: int, start_time: int = 100) -> bytes:
    fields = ["S", *("0" for _ in range(18)), str(start_time), "0", "0"]
    return f"{pid} (python worker) {' '.join(fields)}\n".encode()


def _process(
    root: Path,
    pid: int,
    arguments: list[str],
    cgroup: str,
) -> None:
    directory = root / str(pid)
    directory.mkdir(parents=True)
    (directory / "stat").write_bytes(_stat(pid))
    (directory / "cmdline").write_bytes(
        b"\0".join(argument.encode() for argument in arguments) + b"\0"
    )
    (directory / "cgroup").write_bytes(cgroup.encode())


@pytest.mark.parametrize(
    ("module_name", "arguments"),
    [
        (
            "codev_platform.chroma.daemon_entry",
            [
                "/release/bin/python",
                "-I",
                "-m",
                "codev_platform.chroma.daemon_entry",
                "--http",
            ],
        ),
        (
            "codev_platform.chroma.server",
            ["/release/bin/python", "-I", "-m", "codev_platform.chroma.server", "--http"],
        ),
        (
            "codev_platform.agent.memory_mcp",
            [
                "/release/bin/python",
                "-I",
                "-m",
                "codev_platform.agent.memory_mcp",
                "--http",
                "--port",
                "18087",
            ],
        ),
        (
            "codev_platform.graph.mcp_server",
            [
                "/release/bin/python",
                "-I",
                "-m",
                "codev_platform.graph.mcp_server",
                "--http",
                "--port",
                "18092",
            ],
        ),
    ],
)
def test_精确识别并拒绝固定unit外的MCP写端点(
    tmp_path,
    module_name,
    arguments,
) -> None:
    from codev_platform.runtime_unmanaged_mcp_writer import (
        UnmanagedMCPWriterError,
        assert_no_unmanaged_mcp_writers,
    )

    _process(tmp_path, 301, arguments, "0::/user.slice/user-1000.slice/session-1.scope\n")

    with pytest.raises(UnmanagedMCPWriterError, match="systemd 外"):
        assert_no_unmanaged_mcp_writers(proc_root=tmp_path)

    assert module_name in arguments


@pytest.mark.parametrize(
    ("arguments", "unit"),
    [
        (
            [
                "/release/bin/python",
                "-I",
                "-m",
                "codev_platform.chroma.daemon_entry",
                "--http",
            ],
            "codev-mcp-platform-docs.service",
        ),
        (
            ["/release/bin/python", "-I", "-m", "codev_platform.chroma.server", "--http"],
            "codev-mcp-platform-docs.service",
        ),
        (
            ["/release/bin/python", "-m", "codev_platform.agent.memory_mcp"],
            "codev-mcp-agent-memory.service",
        ),
        (
            ["/release/bin/python", "-m", "codev_platform.graph.mcp_server", "--http"],
            "codev-mcp-graph.service",
        ),
    ],
)
def test_固定unit内的合法进程不误报(tmp_path, arguments, unit) -> None:
    from codev_platform.runtime_unmanaged_mcp_writer import assert_no_unmanaged_mcp_writers

    _process(tmp_path, 302, arguments, f"0::/system.slice/{unit}\n")

    assert_no_unmanaged_mcp_writers(proc_root=tmp_path)


def test_不接受参数中伪造的模块名字符串(tmp_path) -> None:
    from codev_platform.runtime_unmanaged_mcp_writer import assert_no_unmanaged_mcp_writers

    _process(
        tmp_path,
        303,
        [
            "/release/bin/python",
            "-c",
            "print('codev_platform.graph.mcp_server --http')",
        ],
        "0::/user.slice/user-1000.slice/session-1.scope\n",
    )
    _process(
        tmp_path,
        304,
        [
            "/release/bin/python",
            "-m",
            "unrelated.module",
            "codev_platform.chroma.server",
            "--http",
        ],
        "0::/user.slice/user-1000.slice/session-1.scope\n",
    )

    assert_no_unmanaged_mcp_writers(proc_root=tmp_path)


def test_候选进程扫描期间身份漂移时失败关闭(monkeypatch, tmp_path) -> None:
    from codev_platform import runtime_unmanaged_mcp_writer as guard

    _process(
        tmp_path,
        305,
        ["/release/bin/python", "-m", "codev_platform.graph.mcp_server", "--http"],
        "0::/system.slice/codev-mcp-graph.service\n",
    )
    original = guard._read_bounded
    stat_reads = 0

    def _读取(path: Path, limit: int) -> bytes:
        nonlocal stat_reads
        if path.name == "stat":
            stat_reads += 1
            return _stat(305, 100 if stat_reads == 1 else 101)
        return original(path, limit)

    monkeypatch.setattr(guard, "_read_bounded", _读取)

    with pytest.raises(guard.UnmanagedMCPWriterError, match="身份漂移"):
        guard.assert_no_unmanaged_mcp_writers(proc_root=tmp_path)


def test_proc目录PID与stat身份不一致时失败关闭(tmp_path) -> None:
    from codev_platform.runtime_unmanaged_mcp_writer import (
        UnmanagedMCPWriterError,
        assert_no_unmanaged_mcp_writers,
    )

    _process(
        tmp_path,
        306,
        ["/release/bin/python", "-m", "codev_platform.graph.mcp_server", "--http"],
        "0::/system.slice/codev-mcp-graph.service\n",
    )
    (tmp_path / "306" / "stat").write_bytes(_stat(999))

    with pytest.raises(UnmanagedMCPWriterError, match="身份"):
        assert_no_unmanaged_mcp_writers(proc_root=tmp_path)


def test_扫描根目录异常时失败关闭(tmp_path) -> None:
    from codev_platform.runtime_unmanaged_mcp_writer import (
        UnmanagedMCPWriterError,
        assert_no_unmanaged_mcp_writers,
    )

    not_a_directory = tmp_path / "proc"
    not_a_directory.write_text("not proc", encoding="utf-8")

    with pytest.raises(UnmanagedMCPWriterError, match="无法扫描"):
        assert_no_unmanaged_mcp_writers(proc_root=not_a_directory)
