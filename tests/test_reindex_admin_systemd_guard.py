"""WSL reindex 运维前的 systemd/cgroup 停机证明测试。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def _show(
    *,
    active: str = "inactive",
    sub: str = "dead",
    group: str = "/system.slice/codev-reindex.service",
    restart: str = "no",
    returncode: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        returncode=returncode,
        stdout=(
            f"ActiveState={active}\n"
            f"SubState={sub}\n"
            f"ControlGroup={group}\n"
            f"Restart={restart}\n"
        ),
        stderr="不应泄露的 systemctl 原始输出",
    )


def test_systemd停机证明要求服务完全停止重启已抑制且cgroup未填充(tmp_path) -> None:
    from codev_platform.ops.reindex_admin_systemd_guard import (
        verify_codev_reindex_stopped,
    )

    calls: list[tuple[str, ...]] = []
    events: list[Path] = []

    verify_codev_reindex_stopped(
        platform_name="linux",
        command_runner=lambda command, **_kwargs: calls.append(tuple(command)) or _show(),
        cgroup_events_reader=lambda path: events.append(path) or b"populated 0\nfrozen 0\n",
        cgroup_root=tmp_path,
    )

    assert calls == [
        (
            "systemctl",
            "show",
            "codev-reindex.service",
            "--property=ActiveState",
            "--property=SubState",
            "--property=ControlGroup",
            "--property=Restart",
        ),
    ]
    assert events == [tmp_path / "system.slice" / "codev-reindex.service" / "cgroup.events"]


def test_systemd停机证明按字段名解析而不依赖返回顺序(tmp_path) -> None:
    from codev_platform.ops.reindex_admin_systemd_guard import (
        verify_codev_reindex_stopped,
    )

    verify_codev_reindex_stopped(
        platform_name="linux",
        command_runner=lambda _command, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=(
                "Restart=no\n"
                "ControlGroup=/system.slice/codev-reindex.service\n"
                "ActiveState=inactive\n"
                "SubState=dead\n"
            ),
        ),
        cgroup_events_reader=lambda _path: b"populated 0\nfrozen 0\n",
        cgroup_root=tmp_path,
    )


def test_codegraph停机证明要求固定服务已停止基线重启策略匹配且cgroup为空(tmp_path) -> None:
    from codev_platform.ops.reindex_admin_systemd_guard import (
        verify_codev_codegraph_stopped,
    )

    calls: list[tuple[str, ...]] = []
    events: list[Path] = []

    verify_codev_codegraph_stopped(
        platform_name="linux",
        command_runner=lambda command, **_kwargs: calls.append(tuple(command)) or _show(
            group="/system.slice/codev-mcp-codegraph.service",
            restart="always",
        ),
        cgroup_events_reader=lambda path: events.append(path) or b"populated 0\nfrozen 0\n",
        cgroup_root=tmp_path,
    )

    assert calls == [
        (
            "systemctl",
            "show",
            "codev-mcp-codegraph.service",
            "--property=ActiveState",
            "--property=SubState",
            "--property=ControlGroup",
            "--property=Restart",
        ),
    ]
    assert events == [
        tmp_path / "system.slice" / "codev-mcp-codegraph.service" / "cgroup.events"
    ]


@pytest.mark.parametrize(
    "show,events",
    [
        (_show(active="active", sub="running", group="/system.slice/codev-mcp-codegraph.service"), None),
        (_show(restart="no", group="/system.slice/codev-mcp-codegraph.service"), None),
        (_show(group="/system.slice/codev-reindex.service"), None),
        (_show(group="/system.slice/codev-mcp-codegraph.service"), b"populated 1\nfrozen 0\n"),
    ],
)
def test_codegraph停机证明拒绝未停机错误基线cgroup或残留后代(show, events) -> None:
    from codev_platform.ops.reindex_admin_systemd_guard import (
        LegacyWorkerStopProofError,
        verify_codev_codegraph_stopped,
    )

    with pytest.raises(LegacyWorkerStopProofError):
        verify_codev_codegraph_stopped(
            platform_name="linux",
            command_runner=lambda _command, **_kwargs: show,
            cgroup_events_reader=(
                (lambda _path: pytest.fail("无效 unit 状态不得读取 cgroup"))
                if events is None
                else lambda _path: events
            ),
        )


def test_codegraph停机证明拒绝关闭基线重启策略且不读取cgroup(tmp_path) -> None:
    from codev_platform.ops.reindex_admin_systemd_guard import (
        LegacyWorkerStopProofError,
        verify_codev_codegraph_stopped,
    )

    reads: list[Path] = []
    with pytest.raises(LegacyWorkerStopProofError):
        verify_codev_codegraph_stopped(
            platform_name="linux",
            command_runner=lambda _command, **_kwargs: _show(
                group="/system.slice/codev-mcp-codegraph.service",
                restart="no",
            ),
            cgroup_events_reader=lambda path: reads.append(path) or b"populated 0\n",
            cgroup_root=tmp_path,
        )

    assert reads == []


@pytest.mark.parametrize(
    "unit",
    [
        "codev-mcp-codegraph",
        "codev-mcp-codegraph.service/other",
        "codev-mcp-codegraph.service ",
        "codev-mcp-codegraph.service;other",
    ],
)
def test_通用停机证明拒绝非精确安全的service名称(unit: str) -> None:
    from codev_platform.ops.reindex_admin_systemd_guard import (
        LegacyWorkerStopProofError,
        verify_systemd_unit_stopped,
    )

    with pytest.raises(LegacyWorkerStopProofError):
        verify_systemd_unit_stopped(
            unit,
            expected_restart="no",
            platform_name="linux",
            command_runner=lambda _command, **_kwargs: pytest.fail("非法 unit 不得调用 systemctl"),
        )


@pytest.mark.parametrize(
    "show",
    [
        _show(active="active", sub="running"),
        _show(active="inactive", sub="dead", restart="always"),
        _show(active="inactive", sub="stopping"),
    ],
)
def test_systemd停机证明拒绝服务未完全停止或仍可能自动重启(show) -> None:
    from codev_platform.ops.reindex_admin_systemd_guard import (
        LegacyWorkerStopProofError,
        verify_codev_reindex_stopped,
    )

    with pytest.raises(LegacyWorkerStopProofError) as raised:
        verify_codev_reindex_stopped(
            platform_name="linux",
            command_runner=lambda _command, **_kwargs: show,
            cgroup_events_reader=lambda _path: pytest.fail("服务状态不合格时不得读取 cgroup"),
        )

    assert "不应泄露" not in str(raised.value)


def test_systemd停机证明拒绝cgroup后代仍有进程() -> None:
    from codev_platform.ops.reindex_admin_systemd_guard import (
        LegacyWorkerStopProofError,
        verify_codev_reindex_stopped,
    )

    with pytest.raises(LegacyWorkerStopProofError):
        verify_codev_reindex_stopped(
            platform_name="linux",
            command_runner=lambda _command, **_kwargs: _show(),
            cgroup_events_reader=lambda _path: b"populated 1\nfrozen 0\n",
        )


def test_systemd停机证明允许已被systemd移除的空cgroup(tmp_path) -> None:
    from codev_platform.ops.reindex_admin_systemd_guard import (
        verify_codev_reindex_stopped,
    )

    verify_codev_reindex_stopped(
        platform_name="linux",
        command_runner=lambda _command, **_kwargs: _show(),
        cgroup_events_reader=lambda _path: (_ for _ in ()).throw(FileNotFoundError()),
        cgroup_root=tmp_path,
    )


@pytest.mark.parametrize(
    "group",
    [
        "/../missing",
        "/system.slice/../missing",
        "/system.slice/not-codev-reindex.service",
    ],
)
def test_systemd停机证明拒绝越出根目录或不属于目标unit的ControlGroup(group: str, tmp_path) -> None:
    from codev_platform.ops.reindex_admin_systemd_guard import (
        LegacyWorkerStopProofError,
        verify_codev_reindex_stopped,
    )

    reads: list[Path] = []
    with pytest.raises(LegacyWorkerStopProofError):
        verify_codev_reindex_stopped(
            platform_name="linux",
            command_runner=lambda _command, **_kwargs: _show(group=group),
            cgroup_events_reader=lambda path: reads.append(path) or b"populated 0\n",
            cgroup_root=tmp_path,
        )
    assert reads == []


def test_codegraph运行证明要求固定unit运行基线策略与填充cgroup(tmp_path) -> None:
    from codev_platform.ops.reindex_admin_systemd_guard import (
        verify_codev_codegraph_running,
    )

    calls: list[tuple[str, ...]] = []
    events: list[Path] = []

    verify_codev_codegraph_running(
        platform_name="linux",
        command_runner=lambda command, **_kwargs: calls.append(tuple(command)) or _show(
            active="active",
            sub="running",
            group="/system.slice/codev-mcp-codegraph.service",
            restart="always",
        ),
        cgroup_events_reader=lambda path: events.append(path) or b"populated 1\nfrozen 0\n",
        cgroup_root=tmp_path,
    )

    assert calls == [
        (
            "systemctl",
            "show",
            "codev-mcp-codegraph.service",
            "--property=ActiveState",
            "--property=SubState",
            "--property=ControlGroup",
            "--property=Restart",
        ),
    ]
    assert events == [
        tmp_path / "system.slice" / "codev-mcp-codegraph.service" / "cgroup.events"
    ]


@pytest.mark.parametrize(
    "show,events",
    [
        (_show(group="/system.slice/codev-mcp-codegraph.service", restart="always"), None),
        (
            _show(
                active="active",
                sub="running",
                group="/system.slice/codev-mcp-codegraph.service",
                restart="no",
            ),
            None,
        ),
        (
            _show(
                active="active",
                sub="running",
                group="/system.slice/codev-reindex.service",
                restart="always",
            ),
            None,
        ),
        (
            _show(
                active="active",
                sub="running",
                group="/system.slice/codev-mcp-codegraph.service",
                restart="always",
            ),
            b"populated 0\nfrozen 0\n",
        ),
    ],
)
def test_codegraph运行证明拒绝错误状态策略cgroup或空控制组(show, events) -> None:
    from codev_platform.ops.reindex_admin_systemd_guard import (
        LegacyWorkerStopProofError,
        verify_codev_codegraph_running,
    )

    with pytest.raises(LegacyWorkerStopProofError):
        verify_codev_codegraph_running(
            platform_name="linux",
            command_runner=lambda _command, **_kwargs: show,
            cgroup_events_reader=(
                (lambda _path: pytest.fail("状态不合格时不得读取 cgroup"))
                if events is None
                else lambda _path: events
            ),
        )
