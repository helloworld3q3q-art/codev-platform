"""CodeGraph 维护期 runtime mask 的有效 systemd 解析原语。"""
from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable

from codev_platform.core.systemd_unit_resolution import (
    RuntimeMaskState,
    SystemdUnitResolution,
    SystemdUnitResolutionError,
    classify_runtime_mask,
    read_runtime_mask_target,
    read_unit_resolution,
)


_CODEGRAPH_UNIT = "codev-mcp-codegraph.service"
_CANONICAL_CODEGRAPH_UNIT_PATH = "/usr/local/lib/systemd/system/codev-mcp-codegraph.service"
_SYSTEMCTL_TIMEOUT_SEC = 10.0


class CodegraphRuntimeMaskError(RuntimeError):
    """无法安全施加、证明或移除 CodeGraph 的临时启动禁令。"""


CommandRunner = Callable[..., object]
RuntimeMaskTargetReader = Callable[[str], str | None]


def apply_codegraph_runtime_mask(
    *,
    platform_name: str | None = None,
    command_runner: CommandRunner | None = None,
    runtime_mask_target_reader: RuntimeMaskTargetReader | None = None,
) -> None:
    """仅在 canonical 主 unit 已实际生效时施加可逆 runtime mask。"""
    run = _require_linux_runner(platform_name, command_runner)
    target_reader = _require_runtime_mask_target_reader(runtime_mask_target_reader)
    _require_canonical_unmasked_layout(run, target_reader)
    mask_applied = False
    try:
        _run_systemctl(run, ("systemctl", "mask", "--runtime", _CODEGRAPH_UNIT))
        mask_applied = True
        _run_systemctl(run, ("systemctl", "daemon-reload"))
        verify_codegraph_runtime_mask(
            platform_name=platform_name,
            command_runner=run,
            runtime_mask_target_reader=target_reader,
        )
    except (KeyboardInterrupt, SystemExit, MemoryError):
        if mask_applied:
            _settle_failed_mask_application(run, target_reader)
        raise
    except Exception as error:
        if mask_applied:
            try:
                _settle_failed_mask_application(run, target_reader)
            except Exception as settlement_error:
                raise CodegraphRuntimeMaskError("CodeGraph runtime mask 施加失败；安全状态未证明") from settlement_error
        if isinstance(error, CodegraphRuntimeMaskError):
            raise
        raise CodegraphRuntimeMaskError("CodeGraph runtime mask 施加失败") from error


def ensure_codegraph_runtime_mask(
    *,
    platform_name: str | None = None,
    command_runner: CommandRunner | None = None,
    runtime_mask_target_reader: RuntimeMaskTargetReader | None = None,
) -> None:
    """幂等收敛本工具拥有的 runtime mask；持久/残留/未知状态一律拒绝。"""
    run = _require_linux_runner(platform_name, command_runner)
    target_reader = _require_runtime_mask_target_reader(runtime_mask_target_reader)
    state = _runtime_mask_state(run, target_reader)
    if state is RuntimeMaskState.EFFECTIVE_RUNTIME_MASK:
        return
    if state is RuntimeMaskState.UNMASKED:
        apply_codegraph_runtime_mask(
            platform_name=platform_name,
            command_runner=run,
            runtime_mask_target_reader=target_reader,
        )
        return
    raise CodegraphRuntimeMaskError("CodeGraph runtime mask 状态不允许维护收敛")


def verify_codegraph_runtime_mask(
    *,
    platform_name: str | None = None,
    command_runner: CommandRunner | None = None,
    runtime_mask_target_reader: RuntimeMaskTargetReader | None = None,
) -> None:
    """仅接受 `masked-runtime`、`/run` FragmentPath 与 `/dev/null` 链接三重一致。"""
    run = _require_linux_runner(platform_name, command_runner)
    target_reader = _require_runtime_mask_target_reader(runtime_mask_target_reader)
    if _runtime_mask_state(run, target_reader) is not RuntimeMaskState.EFFECTIVE_RUNTIME_MASK:
        raise CodegraphRuntimeMaskError("CodeGraph 临时 systemd mask 无法证明")


def remove_codegraph_runtime_mask(
    *,
    platform_name: str | None = None,
    command_runner: CommandRunner | None = None,
    runtime_mask_target_reader: RuntimeMaskTargetReader | None = None,
) -> None:
    """只移除已证明有效的 runtime mask，持久 mask 与残留状态一律失败关闭。"""
    run = _require_linux_runner(platform_name, command_runner)
    target_reader = _require_runtime_mask_target_reader(runtime_mask_target_reader)
    verify_codegraph_runtime_mask(
        platform_name=platform_name,
        command_runner=run,
        runtime_mask_target_reader=target_reader,
    )
    _run_systemctl(run, ("systemctl", "unmask", "--runtime", _CODEGRAPH_UNIT))
    _run_systemctl(run, ("systemctl", "daemon-reload"))
    _require_canonical_unmasked_layout(run, target_reader)


def _settle_failed_mask_application(
    run: CommandRunner,
    target_reader: RuntimeMaskTargetReader,
) -> None:
    """mask 证明失败后仅清理本工具的 runtime 层，并证明 canonical 常态已恢复。"""
    _run_systemctl(run, ("systemctl", "unmask", "--runtime", _CODEGRAPH_UNIT))
    _run_systemctl(run, ("systemctl", "daemon-reload"))
    _require_canonical_unmasked_layout(run, target_reader)


def _require_canonical_unmasked_layout(
    run: CommandRunner,
    target_reader: RuntimeMaskTargetReader,
) -> None:
    resolution = _read_resolution(run)
    state = _classify_resolution(resolution, target_reader)
    if state is not RuntimeMaskState.UNMASKED:
        raise CodegraphRuntimeMaskError("CodeGraph runtime mask 状态不允许切换")
    if resolution.load_state != "loaded" or resolution.fragment_path != _CANONICAL_CODEGRAPH_UNIT_PATH:
        raise CodegraphRuntimeMaskError(
            "CodeGraph 主 unit 尚未迁移到 canonical 目录，请先执行布局迁移"
        )


def _runtime_mask_state(
    run: CommandRunner,
    target_reader: RuntimeMaskTargetReader,
) -> RuntimeMaskState:
    return _classify_resolution(_read_resolution(run), target_reader)


def _classify_resolution(
    resolution: SystemdUnitResolution,
    target_reader: RuntimeMaskTargetReader,
) -> RuntimeMaskState:
    try:
        target = target_reader(_CODEGRAPH_UNIT)
        return classify_runtime_mask(resolution, runtime_mask_target=target)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise CodegraphRuntimeMaskError("CodeGraph runtime mask 状态不可用") from error


def _read_resolution(run: CommandRunner) -> SystemdUnitResolution:
    try:
        return read_unit_resolution(_CODEGRAPH_UNIT, command_runner=run)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except SystemdUnitResolutionError as error:
        raise CodegraphRuntimeMaskError("CodeGraph systemd mask 状态格式无效") from error


def _require_runtime_mask_target_reader(
    reader: RuntimeMaskTargetReader | None,
) -> RuntimeMaskTargetReader:
    selected = read_runtime_mask_target if reader is None else reader
    if not callable(selected):
        raise CodegraphRuntimeMaskError("CodeGraph runtime mask 适配器不可用")
    return selected


def _require_linux_runner(
    platform_name: str | None,
    command_runner: CommandRunner | None,
) -> CommandRunner:
    platform = sys.platform if platform_name is None else platform_name
    if type(platform) is not str or not platform.startswith("linux"):
        raise CodegraphRuntimeMaskError("当前平台不支持 CodeGraph systemd 临时 mask")
    run = _default_command_runner if command_runner is None else command_runner
    if not callable(run):
        raise CodegraphRuntimeMaskError("CodeGraph systemd 临时 mask 适配器不可用")
    return run


def _run_systemctl(run: CommandRunner, command: tuple[str, ...]) -> object:
    try:
        result = run(command, timeout_sec=_SYSTEMCTL_TIMEOUT_SEC)
    except MemoryError:
        raise
    except Exception as error:
        raise CodegraphRuntimeMaskError("CodeGraph systemd 维护命令无法执行") from error
    if getattr(result, "returncode", None) != 0:
        raise CodegraphRuntimeMaskError("CodeGraph systemd 维护命令失败")
    return result


def _default_command_runner(command: tuple[str, ...], *, timeout_sec: float) -> object:
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_sec,
        check=False,
    )


__all__ = [
    "CodegraphRuntimeMaskError",
    "apply_codegraph_runtime_mask",
    "ensure_codegraph_runtime_mask",
    "remove_codegraph_runtime_mask",
    "verify_codegraph_runtime_mask",
]
