"""维护窗口对 systemd 外 reindex worker 与直接写入入口的 fail-closed 进程证明。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

from codev_platform.core.systemd_process_identity import (
    SystemdProcessIdentityError,
    process_belongs_to_systemd_unit,
)

_REINDEX_UNIT = "codev-reindex.service"
_MAX_CMDLINE_BYTES = 16 * 1024
_DEFAULT_MAX_PROCESSES = 131072
_SELF_CGROUP_PATH = Path("/proc/self/cgroup")
_FOREGROUND_HOOK_ACTIONS = frozenset(
    {
        "post-commit",
        "post-merge",
        "post-checkout",
    }
)
_DIRECT_WRITER_MODULES = frozenset(
    {
        "codev_platform.chroma.indexer",
        "codev_platform.recall.code_vector_store",
    }
)
_CODEGRAPH_PROXY_MODULE = "codev_platform.codegraph.server"
_CODEGRAPH_WRITE_ACTIONS = frozenset({"init", "index", "sync", "uninit"})
_NODE_EXECUTABLES = frozenset({"node", "node.exe", "nodejs", "nodejs.exe"})
_POSIX_SHELL_EXECUTABLES = frozenset({"ash", "bash", "dash", "ksh", "sh", "zsh"})
_CODEGRAPH_NPM_SHIM_SUFFIX = (
    "node_modules",
    "@colbymchenry",
    "codegraph",
    "npm-shim.js",
)
_CODEGRAPH_BUNDLED_SCRIPT_SUFFIX = ("lib", "dist", "bin", "codegraph.js")
_CODEGRAPH_SHELL_LAUNCHER_SUFFIX = ("bin", "codegraph")
_NODE_OPTIONS_WITH_SEPARATE_VALUE = frozenset(
    {
        "-C",
        "-r",
        "--conditions",
        "--experimental-loader",
        "--import",
        "--loader",
        "--require",
    }
)

ProcessMatcher = Callable[[Path, tuple[str, ...]], bool]


class ExternalReindexWorkerError(RuntimeError):
    """无法证明不存在 systemd 外的受控 reindex 进程。"""


def assert_no_external_reindex_workers(
    *,
    proc_root: Path = Path("/proc"),
    max_processes: int = _DEFAULT_MAX_PROCESSES,
) -> None:
    """扫描精确 CLI 形态；扫描不完整或发现外部 worker 均拒绝维护写操作。"""
    _assert_no_external_reindex_processes(
        proc_root=proc_root,
        max_processes=max_processes,
        matcher=_is_reindex_worker,
        label="worker",
    )


def assert_no_external_reindex_writers(
    *,
    proc_root: Path = Path("/proc"),
    max_processes: int = _DEFAULT_MAX_PROCESSES,
) -> None:
    """证明没有 systemd 外的已知 reindex 写入口，供维护迁移与 owner 初始化使用。"""
    _assert_no_external_reindex_processes(
        proc_root=proc_root,
        max_processes=max_processes,
        matcher=_is_reindex_writer,
        label="写入者",
    )


def _assert_no_external_reindex_processes(
    *,
    proc_root: Path,
    max_processes: int,
    matcher: ProcessMatcher,
    label: str,
) -> None:
    """在有界 /proc 扫描中验证候选进程均属于受控 systemd unit。"""
    root = Path(proc_root)
    if not root.is_absolute() or type(max_processes) is not int or max_processes <= 0:
        raise ExternalReindexWorkerError(f"外部 reindex {label} 进程证明配置无效")
    count = 0
    try:
        entries = list(root.iterdir())
    except OSError as error:
        raise ExternalReindexWorkerError(f"无法扫描外部 reindex {label}") from error
    for entry in entries:
        if not entry.name.isascii() or not entry.name.isdigit():
            continue
        count += 1
        if count > max_processes:
            raise ExternalReindexWorkerError(f"外部 reindex {label} 扫描不完整")
        _assert_entry_safe(entry, matcher=matcher, label=label)


def current_process_in_reindex_unit_cgroup(
    *,
    cgroup_path: Path = _SELF_CGROUP_PATH,
) -> bool:
    """仅在当前进程可证明属于受控 reindex unit 时返回真。"""
    return current_process_in_systemd_unit_cgroup(
        _REINDEX_UNIT,
        cgroup_path=cgroup_path,
    )


def current_process_in_systemd_unit_cgroup(
    unit: str,
    *,
    cgroup_path: Path = _SELF_CGROUP_PATH,
) -> bool:
    """仅在当前进程精确属于指定受管 systemd service cgroup 时返回真。"""
    try:
        path = Path(cgroup_path)
        if not path.is_absolute():
            return False
        return process_belongs_to_systemd_unit(unit, cgroup_path=path)
    except (SystemdProcessIdentityError, OSError, TypeError, ValueError):
        return False


def _assert_entry_safe(entry: Path, *, matcher: ProcessMatcher, label: str) -> None:
    try:
        arguments = _read_arguments(entry / "cmdline")
    except FileNotFoundError:
        return
    except OSError as error:
        raise ExternalReindexWorkerError(f"外部 reindex {label} 扫描不完整") from error
    if not matcher(entry, arguments):
        return
    try:
        managed = process_belongs_to_systemd_unit(
            _REINDEX_UNIT,
            cgroup_path=entry / "cgroup",
        )
    except (SystemdProcessIdentityError, OSError) as error:
        raise ExternalReindexWorkerError(f"候选 reindex {label} cgroup 不可证明") from error
    if not managed:
        raise ExternalReindexWorkerError(f"发现 systemd 外 reindex {label}，拒绝维护写操作")


def _read_arguments(path: Path) -> tuple[str, ...]:
    raw = path.read_bytes()
    if len(raw) > _MAX_CMDLINE_BYTES:
        raise ExternalReindexWorkerError("外部 reindex worker 命令行超过上限")
    if not raw:
        return ()
    if raw[-1:] != b"\0":
        raise ExternalReindexWorkerError("外部 reindex worker 命令行格式无效")
    return tuple(part.decode("utf-8", "surrogateescape") for part in raw[:-1].split(b"\0"))


def _is_reindex_worker(_entry: Path, arguments: tuple[str, ...]) -> bool:
    return any(
        action == "reindex-queue" and remainder[:1] == ("worker",)
        for action, remainder in _iter_cli_actions(arguments)
    )


def _is_reindex_writer(entry: Path, arguments: tuple[str, ...]) -> bool:
    """只识别已知 CLI 写入口，避免把普通 hook 或诊断命令误判为写进程。"""
    if (
        _is_direct_module_writer(arguments)
        or _is_codegraph_proxy_parent(arguments)
        or _is_codegraph_writer(entry, arguments)
    ):
        return True
    for action, remainder in _iter_cli_actions(arguments):
        if action == "reindex":
            return True
        if action == "graph" and remainder[:1] == ("ingest",):
            return True
        if action == "reindex-queue" and remainder[:1] in {("worker",), ("drain-once",)}:
            return True
        if action in _FOREGROUND_HOOK_ACTIONS and "--foreground" in remainder:
            return True
    return False


def _is_direct_module_writer(arguments: tuple[str, ...]) -> bool:
    """识别绕过平台 CLI 的正式索引模块入口。"""
    for module, remainder in _iter_python_modules(arguments):
        if module not in _DIRECT_WRITER_MODULES:
            continue
        if module == "codev_platform.chroma.indexer" and "--dry-run" in remainder:
            continue
        return True
    return False


def _is_codegraph_proxy_parent(arguments: tuple[str, ...]) -> bool:
    """精确识别 Python 尚未 exec 到 CodeGraph 子 CLI 的代理父进程。"""
    for index in range(1, len(arguments) - 1):
        if arguments[index : index + 2] != ("-m", _CODEGRAPH_PROXY_MODULE):
            continue
        if _is_python_executable(arguments[index - 1]):
            return True
    return False


def _is_python_executable(value: str) -> bool:
    """接受常见 Python 解释器和 Windows launcher，避免普通进程误判。"""
    name = Path(value).name.lower()
    base = name[:-4] if name.endswith(".exe") else name
    if base in {"py", "python"}:
        return True
    if not base.startswith("python"):
        return False
    version = base.removeprefix("python").replace(".", "")
    return bool(version) and version.isdigit()


def _is_codegraph_writer(_entry: Path, arguments: tuple[str, ...]) -> bool:
    """识别 CodeGraph 索引写入口；维护期所有 serve 都视为潜在写者。"""
    parsed = _codegraph_action(arguments)
    if parsed is None:
        return False
    action, _ = parsed
    if action in _CODEGRAPH_WRITE_ACTIONS:
        return True
    return action == "serve"


def _codegraph_action(arguments: tuple[str, ...]) -> tuple[str, tuple[str, ...]] | None:
    """解析直接 CLI、官方 shell launcher 与 node 包装的 CodeGraph 命令。"""
    if len(arguments) >= 2 and _is_codegraph_executable(arguments[0]):
        return arguments[1], arguments[2:]
    launcher_index = _codegraph_shell_launcher_index(arguments)
    if launcher_index is not None and launcher_index + 1 < len(arguments):
        return arguments[launcher_index + 1], arguments[launcher_index + 2 :]
    script_index = _codegraph_node_script_index(arguments)
    if script_index is not None and script_index + 1 < len(arguments):
        return arguments[script_index + 1], arguments[script_index + 2 :]
    return None


def _is_codegraph_executable(value: str) -> bool:
    return Path(value).name.lower() in {"codegraph", "codegraph.exe"}


def _codegraph_shell_launcher_index(arguments: tuple[str, ...]) -> int | None:
    """识别官方 POSIX shell launcher 尚未 exec 到 bundled node 的短暂窗口。"""
    if len(arguments) < 2:
        return None
    if Path(arguments[0]).name.lower() not in _POSIX_SHELL_EXECUTABLES:
        return None
    return 1 if _is_codegraph_shell_launcher(arguments[1]) else None


def _is_codegraph_shell_launcher(script: str) -> bool:
    """只接受位于 ``bin/codegraph`` 的受控 launcher，避免误判普通 shell 命令。"""
    parts = tuple(part for part in script.replace("\\", "/").split("/") if part)
    return parts[-len(_CODEGRAPH_SHELL_LAUNCHER_SUFFIX) :] == _CODEGRAPH_SHELL_LAUNCHER_SUFFIX


def _codegraph_node_script_index(arguments: tuple[str, ...]) -> int | None:
    """从 node 前置 flag 后定位受控 CodeGraph 脚本，兼容 bundled runtime。"""
    if not arguments or Path(arguments[0]).name.lower() not in _NODE_EXECUTABLES:
        return None
    index = 1
    while index < len(arguments):
        argument = arguments[index]
        if argument in _NODE_OPTIONS_WITH_SEPARATE_VALUE:
            index += 2
            continue
        if argument.startswith("-"):
            index += 1
            continue
        return index if _is_codegraph_node_script(argument) else None
    return None


def _is_codegraph_node_script(script: str) -> bool:
    """只接受官方 npm shim 或 bundled CLI 脚本，避免把任意 Node 进程当成写者。"""
    if _is_codegraph_executable(script):
        return True
    parts = tuple(part for part in script.replace("\\", "/").split("/") if part)
    if parts[-len(_CODEGRAPH_NPM_SHIM_SUFFIX) :] == _CODEGRAPH_NPM_SHIM_SUFFIX:
        return True
    if parts[-len(_CODEGRAPH_BUNDLED_SCRIPT_SUFFIX) :] != _CODEGRAPH_BUNDLED_SCRIPT_SUFFIX:
        return False
    return any(
        part == "codegraph" or part.startswith("codegraph-")
        for part in parts[: -len(_CODEGRAPH_BUNDLED_SCRIPT_SUFFIX)]
    )


def _iter_python_modules(arguments: tuple[str, ...]) -> Iterator[tuple[str, tuple[str, ...]]]:
    """迭代 Python ``-m`` 的模块名及其余参数。"""
    for index in range(max(0, len(arguments) - 1)):
        if arguments[index] == "-m":
            yield arguments[index + 1], arguments[index + 2 :]


def _iter_cli_actions(arguments: tuple[str, ...]) -> Iterator[tuple[str, tuple[str, ...]]]:
    """迭代 python -m 与 console-script 两种精确 CLI action 边界。"""
    module_prefix = ("-m", "codev_platform.cli")
    for index in range(max(0, len(arguments) - len(module_prefix))):
        if arguments[index : index + len(module_prefix)] != module_prefix:
            continue
        action_index = index + len(module_prefix)
        if action_index < len(arguments):
            yield arguments[action_index], arguments[action_index + 1 :]
    for index in range(max(0, len(arguments) - 1)):
        if Path(arguments[index]).name != "codev-platform":
            continue
        yield arguments[index + 1], arguments[index + 2 :]


__all__ = [
    "ExternalReindexWorkerError",
    "assert_no_external_reindex_workers",
    "assert_no_external_reindex_writers",
    "current_process_in_reindex_unit_cgroup",
    "current_process_in_systemd_unit_cgroup",
]
