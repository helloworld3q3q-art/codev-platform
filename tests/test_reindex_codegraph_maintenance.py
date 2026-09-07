"""CodeGraph 维护期 runtime mask 的可逆启动禁令测试。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def _ok(*, stdout: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="不得泄露原始 systemctl 输出")


def _masked_state(*, unit_file_state: str = "masked-runtime") -> SimpleNamespace:
    return _ok(
        stdout=(
            "LoadState=masked\n"
            f"UnitFileState={unit_file_state}\n"
            "FragmentPath=/run/systemd/system/codev-mcp-codegraph.service\n"
        ),
    )


def _loaded_state(
    *,
    unit_file_state: str = "enabled",
    fragment_path: str = "/usr/local/lib/systemd/system/codev-mcp-codegraph.service",
) -> SimpleNamespace:
    return _ok(
        stdout=(
            "LoadState=loaded\n"
            f"UnitFileState={unit_file_state}\n"
            f"FragmentPath={fragment_path}\n"
        ),
    )


def test_应用runtime_mask只操作固定codegraph单元并复核临时掩码() -> None:
    from codev_platform.ops.reindex_codegraph_maintenance import (
        apply_codegraph_runtime_mask,
    )

    calls: list[tuple[str, ...]] = []
    states = iter((_loaded_state(), _masked_state()))

    def run(command: tuple[str, ...], **_kwargs) -> SimpleNamespace:
        calls.append(command)
        return next(states) if command[1] == "show" else _ok()

    apply_codegraph_runtime_mask(
        platform_name="linux",
        command_runner=run,
        runtime_mask_target_reader=lambda _unit: (
            "/dev/null"
            if ("systemctl", "mask", "--runtime", "codev-mcp-codegraph.service") in calls
            else None
        ),
    )

    assert calls == [
        (
            "systemctl",
            "show",
            "codev-mcp-codegraph.service",
            "--property=LoadState",
            "--property=UnitFileState",
            "--property=FragmentPath",
        ),
        ("systemctl", "mask", "--runtime", "codev-mcp-codegraph.service"),
        ("systemctl", "daemon-reload"),
        (
            "systemctl",
            "show",
            "codev-mcp-codegraph.service",
            "--property=LoadState",
            "--property=UnitFileState",
            "--property=FragmentPath",
        ),
    ]


@pytest.mark.parametrize(
    "state",
    [
        _loaded_state(),
        _masked_state(unit_file_state="masked"),
        _ok(stdout="LoadState=masked\n"),
        _ok(stdout="LoadState=masked\nUnitFileState=masked-runtime\nUnitFileState=masked-runtime\nFragmentPath=/run/systemd/system/codev-mcp-codegraph.service\n"),
        _ok(stdout="LoadState=masked\nUnitFileState=masked-runtime\nFragmentPath=/etc/systemd/system/codev-mcp-codegraph.service\n"),
    ],
)
def test_runtime_mask证明拒绝非临时掩码或格式异常(state: SimpleNamespace) -> None:
    from codev_platform.ops.reindex_codegraph_maintenance import (
        CodegraphRuntimeMaskError,
        verify_codegraph_runtime_mask,
    )

    with pytest.raises(CodegraphRuntimeMaskError):
        verify_codegraph_runtime_mask(
            platform_name="linux",
            command_runner=lambda _command, **_kwargs: state,
            runtime_mask_target_reader=lambda _unit: "/dev/null",
        )


def test_解除mask只解除runtime层且保留持久mask时失败关闭() -> None:
    from codev_platform.ops.reindex_codegraph_maintenance import (
        CodegraphRuntimeMaskError,
        remove_codegraph_runtime_mask,
    )

    calls: list[tuple[str, ...]] = []
    states = iter((_masked_state(), _masked_state(unit_file_state="masked")))

    def run(command: tuple[str, ...], **_kwargs) -> SimpleNamespace:
        calls.append(command)
        return next(states) if command[1] == "show" else _ok()

    with pytest.raises(CodegraphRuntimeMaskError):
        remove_codegraph_runtime_mask(
            platform_name="linux",
            command_runner=run,
            runtime_mask_target_reader=lambda _unit: (
                None
                if ("systemctl", "unmask", "--runtime", "codev-mcp-codegraph.service") in calls
                else "/dev/null"
            ),
        )

    assert calls == [
        (
            "systemctl",
            "show",
            "codev-mcp-codegraph.service",
            "--property=LoadState",
            "--property=UnitFileState",
            "--property=FragmentPath",
        ),
        ("systemctl", "unmask", "--runtime", "codev-mcp-codegraph.service"),
        ("systemctl", "daemon-reload"),
        (
            "systemctl",
            "show",
            "codev-mcp-codegraph.service",
            "--property=LoadState",
            "--property=UnitFileState",
            "--property=FragmentPath",
        ),
    ]


def test_人工持久mask不属于本工具且不得调用解除命令() -> None:
    """维护恢复仅拥有 runtime mask，不能隐式修改人工持久配置。"""
    from codev_platform.ops.reindex_codegraph_maintenance import (
        CodegraphRuntimeMaskError,
        remove_codegraph_runtime_mask,
    )

    calls: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], **_kwargs) -> SimpleNamespace:
        calls.append(command)
        return _masked_state(unit_file_state="masked")

    with pytest.raises(CodegraphRuntimeMaskError):
        remove_codegraph_runtime_mask(
            platform_name="linux",
            command_runner=run,
            runtime_mask_target_reader=lambda _unit: "/dev/null",
        )

    assert calls == [
        (
            "systemctl",
            "show",
            "codev-mcp-codegraph.service",
            "--property=LoadState",
            "--property=UnitFileState",
            "--property=FragmentPath",
        ),
    ]


def test_解除runtime_mask后仅接受不再被任何mask阻断的状态() -> None:
    from codev_platform.ops.reindex_codegraph_maintenance import (
        remove_codegraph_runtime_mask,
    )

    calls: list[tuple[str, ...]] = []
    states = iter((_masked_state(), _loaded_state()))

    def run(command: tuple[str, ...], **_kwargs) -> SimpleNamespace:
        calls.append(command)
        return next(states) if command[1] == "show" else _ok()

    remove_codegraph_runtime_mask(
        platform_name="linux",
        command_runner=run,
        runtime_mask_target_reader=lambda _unit: (
            None
            if ("systemctl", "unmask", "--runtime", "codev-mcp-codegraph.service") in calls
            else "/dev/null"
        ),
    )

    assert ("systemctl", "unmask", "--runtime", "codev-mcp-codegraph.service") in calls


def test_非linux环境拒绝修改或证明runtime_mask() -> None:
    from codev_platform.ops.reindex_codegraph_maintenance import (
        CodegraphRuntimeMaskError,
        apply_codegraph_runtime_mask,
    )

    with pytest.raises(CodegraphRuntimeMaskError):
        apply_codegraph_runtime_mask(
            platform_name="win32",
            command_runner=lambda _command, **_kwargs: pytest.fail("非 Linux 不得调用 systemctl"),
        )


def test_legacy主unit会在施加mask前被明确拒绝() -> None:
    """/etc 优先级会吞掉 runtime mask，必须先完成 canonical 布局迁移。"""
    from codev_platform.ops.reindex_codegraph_maintenance import (
        CodegraphRuntimeMaskError,
        apply_codegraph_runtime_mask,
    )

    calls: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], **_kwargs) -> SimpleNamespace:
        calls.append(command)
        return _loaded_state(
            fragment_path="/etc/systemd/system/codev-mcp-codegraph.service"
        )

    with pytest.raises(CodegraphRuntimeMaskError, match="迁移"):
        apply_codegraph_runtime_mask(
            platform_name="linux",
            command_runner=run,
            runtime_mask_target_reader=lambda _unit: None,
        )

    assert calls == [
        (
            "systemctl",
            "show",
            "codev-mcp-codegraph.service",
            "--property=LoadState",
            "--property=UnitFileState",
            "--property=FragmentPath",
        )
    ]


def test_mask施加后无法证明时会清理本次runtime残留并恢复canonical常态() -> None:
    """旧实现失败后遗留 /run->/dev/null；新实现必须在失败路径受控收敛。"""
    from codev_platform.ops.reindex_codegraph_maintenance import (
        CodegraphRuntimeMaskError,
        apply_codegraph_runtime_mask,
    )

    calls: list[tuple[str, ...]] = []
    states = iter((_loaded_state(), _loaded_state(), _loaded_state()))

    def run(command: tuple[str, ...], **_kwargs) -> SimpleNamespace:
        calls.append(command)
        return next(states) if command[1] == "show" else _ok()

    with pytest.raises(CodegraphRuntimeMaskError, match="无法证明"):
        apply_codegraph_runtime_mask(
            platform_name="linux",
            command_runner=run,
            runtime_mask_target_reader=lambda _unit: (
                None
                if ("systemctl", "unmask", "--runtime", "codev-mcp-codegraph.service") in calls
                else (
                    "/dev/null"
                    if ("systemctl", "mask", "--runtime", "codev-mcp-codegraph.service") in calls
                    else None
                )
            ),
        )

    assert calls == [
        (
            "systemctl",
            "show",
            "codev-mcp-codegraph.service",
            "--property=LoadState",
            "--property=UnitFileState",
            "--property=FragmentPath",
        ),
        ("systemctl", "mask", "--runtime", "codev-mcp-codegraph.service"),
        ("systemctl", "daemon-reload"),
        (
            "systemctl",
            "show",
            "codev-mcp-codegraph.service",
            "--property=LoadState",
            "--property=UnitFileState",
            "--property=FragmentPath",
        ),
        ("systemctl", "unmask", "--runtime", "codev-mcp-codegraph.service"),
        ("systemctl", "daemon-reload"),
        (
            "systemctl",
            "show",
            "codev-mcp-codegraph.service",
            "--property=LoadState",
            "--property=UnitFileState",
            "--property=FragmentPath",
        ),
    ]


def test_mask命令本身失败时不得擅自解除其他调用者的runtime_hold() -> None:
    """只有本次 mask 已成功返回，失败补偿才拥有 runtime unmask 的权限。"""
    from codev_platform.ops.reindex_codegraph_maintenance import (
        CodegraphRuntimeMaskError,
        apply_codegraph_runtime_mask,
    )

    calls: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], **_kwargs) -> SimpleNamespace:
        calls.append(command)
        if command[1] == "show":
            return _loaded_state()
        if command[1] == "mask":
            return SimpleNamespace(returncode=1, stdout="", stderr="不得泄露")
        return _ok()

    with pytest.raises(CodegraphRuntimeMaskError):
        apply_codegraph_runtime_mask(
            platform_name="linux",
            command_runner=run,
            runtime_mask_target_reader=lambda _unit: None,
        )

    assert ("systemctl", "unmask", "--runtime", "codev-mcp-codegraph.service") not in calls
