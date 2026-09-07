"""目标服务用户探针的固定子进程协议。"""

from __future__ import annotations

from collections.abc import Callable
import json
import os

from codev_platform.core.runtime_models import BaseMetadata, ReleaseMetadata
from codev_platform.runtime_managed_process import (
    ManagedProcessResult,
    ManagedProcessSpec,
    run_managed_process,
)
from codev_platform.runtime_managed_profiles import TARGET_USER_PROBE_LIMITS
from codev_platform.runtime_release_environment import MANAGED_IMPORTS
from codev_platform.runtime_target_user_layout import TargetProbeLayout, TargetProbeRequest


PROBE_SUCCESS_OUTPUT = b"CODEV_PLATFORM_TARGET_USER_PROBE_OK_V1\n"
_CAPABILITY_FIELDS = ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
_MANAGED_IMPORTS_LITERAL = repr(MANAGED_IMPORTS)
_PROBE_PROJECT_ID = "codev-target-probe"
_PROBE_SCRIPT = f"""
import importlib,json,os,sys,warnings
from pathlib import Path
MANAGED_IMPORTS = {_MANAGED_IMPORTS_LITERAL}
CAPABILITY_FIELDS = {repr(_CAPABILITY_FIELDS)}
PROBE_PROJECT_ID = {_PROBE_PROJECT_ID!r}
SUCCESS = {PROBE_SUCCESS_OUTPUT.decode("ascii").rstrip()!r}
def require(condition):
    if not condition: raise RuntimeError("probe")
def process_state():
    require(dict(os.environ) == expected_environment)
    require(os.getcwd() == "/")
    require(os.getuid() == os.geteuid() == uid)
    require(os.getgid() == os.getegid() == gid)
    require(os.getresuid() == (uid, uid, uid))
    require(os.getresgid() == (gid, gid, gid))
    require(os.getgroups() == [])
    require(sys.flags.isolated == 1)
    require(sys.flags.no_user_site == 1)
    require(sys.flags.dont_write_bytecode == 1)
    require(Path(sys.prefix).resolve(strict=True) == expected_prefix)
    status = {{}}
    selected = ("Uid", "Gid", "NoNewPrivs", *CAPABILITY_FIELDS)
    with open("/proc/self/status", encoding="ascii") as stream:
        for line in stream:
            key, separator, value = line.partition(":")
            if separator and key in selected:
                require(key not in status)
                status[key] = value.strip()
    require(set(status) == set(selected))
    require(status["Uid"].split() == [str(uid)] * 4)
    require(status["Gid"].split() == [str(gid)] * 4)
    require(status["NoNewPrivs"] == "1")
    require(all(int(status[key], 16) == 0 for key in CAPABILITY_FIELDS))
    paths = tuple(Path(value).resolve() for value in sys.path if value)
    require(app_purelib in paths and base_purelib in paths)
    require(paths.index(app_purelib) < paths.index(base_purelib))
    return paths
def require_trusted_base_addition(path, paths, path_index):
    require(path.is_dir())
    require(path.is_relative_to(base_purelib))
    require(path != base_purelib)
    require(path_index > paths.index(app_purelib))
    require(path_index > paths.index(base_purelib))
    metadata = path.stat()
    require(metadata.st_uid == 0)
    require((metadata.st_mode & 0o022) == 0)
    require(not (path / "codev_platform").exists())
    require(not (path / "torch").exists())
def validate_post_import_paths(before_paths):
    after_paths = process_state()
    before_index = 0
    for path_index, path in enumerate(after_paths):
        if before_index < len(before_paths) and path == before_paths[before_index]:
            before_index += 1
        else:
            require_trusted_base_addition(path, after_paths, path_index)
    require(before_index == len(before_paths))
def load_managed_modules():
    os.environ["PLATFORM_PROJECT_ID"] = PROBE_PROJECT_ID
    try:
        with warnings.catch_warnings(record=True) as caught:
            loaded = [(name, importlib.import_module(name)) for name in MANAGED_IMPORTS]
        require_known_import_warnings(caught)
        return loaded
    finally:
        os.environ.pop("PLATFORM_PROJECT_ID", None)
def require_known_import_warnings(caught):
    if not caught:
        return
    syntax_count = 0
    user_count = 0
    for item in caught:
        source = Path(item.filename).resolve()
        require(source.is_relative_to(base_purelib / "jieba"))
        if item.category is SyntaxWarning:
            syntax_count += 1
        elif item.category is UserWarning:
            user_count += 1
        else:
            require(False)
    require(syntax_count == 3)
    require(user_count == 1)
try:
    require(len(sys.argv) == 8)
    uid = int(sys.argv[1])
    gid = int(sys.argv[2])
    release_file = sys.argv[3]
    expected_prefix = Path(sys.argv[4]).resolve(strict=True)
    app_purelib = Path(sys.argv[5]).resolve(strict=True)
    base_purelib = Path(sys.argv[6]).resolve(strict=True)
    expected_identity = json.loads(sys.argv[7])
    expected_environment = {{"CODEV_PLATFORM_RELEASE_FILE": release_file, "HOME": "/", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin"}}
    require(set(MANAGED_IMPORTS).isdisjoint(sys.modules))
    require("torch" not in sys.modules)
    before_paths = process_state()
    from codev_platform.core.runtime_identity import runtime_identity
    require(runtime_identity().as_dict() == expected_identity)
    loaded = load_managed_modules()
    require(all(
        type(getattr(module, "__file__", None)) is str
        and Path(module.__file__).resolve(strict=True).is_relative_to(app_purelib)
        and sys.modules.get(name) is module
        for name, module in loaded
    ))
    torch = importlib.import_module("torch")
    require(type(getattr(torch, "__file__", None)) is str)
    require(Path(torch.__file__).resolve(strict=True).is_relative_to(base_purelib))
    validate_post_import_paths(before_paths)
    require(runtime_identity().as_dict() == expected_identity)
    print(SUCCESS, flush=True)
except BaseException:
    raise SystemExit(70) from None
"""


class RuntimeTargetUserProtocolError(RuntimeError):
    """探针命令、回执或执行器不满足固定协议。"""


def build_probe_command(
    request: TargetProbeRequest,
    base: BaseMetadata,
    release: ReleaseMetadata,
    layout: TargetProbeLayout,
) -> tuple[str, ...]:
    """构造不含可选策略面的固定探针命令。"""
    expected_identity = {
        "base_id": request.base_id,
        "base_requirements_sha256": base.requirements_sha256,
        "environment_prefix": os.fspath(layout.prefix.resolve(strict=True)),
        "interpreter_realpath": os.fspath(layout.resolved_python),
        "mode": "release",
        "release_id": request.release_id,
        "runtime_revision": release.runtime_revision,
        "source_root": None,
        "wheel_sha256": release.wheel_sha256,
    }
    return (
        os.fspath(layout.python),
        "-I",
        "-B",
        "-c",
        _PROBE_SCRIPT,
        str(request.account.uid),
        str(request.account.gid),
        os.fspath(layout.release_metadata),
        os.fspath(layout.prefix),
        os.fspath(layout.app_purelib),
        os.fspath(layout.base_purelib),
        json.dumps(
            expected_identity,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def require_success_output(completed: ManagedProcessResult) -> None:
    """只接受唯一成功行、空标准错误和零退出码。"""
    if (
        type(completed) is not ManagedProcessResult
        or completed.returncode != 0
        or completed.stdout != PROBE_SUCCESS_OUTPUT
        or completed.stderr != b""
    ):
        raise RuntimeTargetUserProtocolError("目标用户探针回执无效")


def run_probe_process(
    argv: tuple[str, ...],
    *,
    runner: Callable[[ManagedProcessSpec], ManagedProcessResult] = run_managed_process,
) -> ManagedProcessResult:
    """在固定资源档位和稳定语义槽内执行一次探针。"""
    result = runner(
        ManagedProcessSpec(
            unit_slot="target-user-probe",
            argv=argv,
            working_directory="/",
            environment=(),
            environment_file=None,
            user=None,
            limits=TARGET_USER_PROBE_LIMITS,
        )
    )
    if type(result) is not ManagedProcessResult:
        raise RuntimeTargetUserProtocolError("目标用户探针执行结果无效")
    return result


__all__ = [
    "PROBE_SUCCESS_OUTPUT",
    "RuntimeTargetUserProtocolError",
    "build_probe_command",
    "require_success_output",
    "run_probe_process",
]
