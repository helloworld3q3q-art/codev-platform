"""目标用户探针导入后路径白名单的 Linux root 回归。"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Literal

import pytest

from codev_platform.runtime_service_process import build_service_process_argv
from codev_platform.runtime_target_user_layout import (
    TargetProbeRequest,
    derive_probe_layout,
    probe_environment,
)
from codev_platform.runtime_target_user_protocol import (
    PROBE_SUCCESS_OUTPUT,
    build_probe_command,
)
from tests.runtime_target_user_probe_support import (
    TargetProbeRuntime,
    build_target_probe_runtime,
)


_ROOT_PROBE_AVAILABLE = (
    os.name == "posix"
    and sys.platform.startswith("linux")
    and hasattr(os, "geteuid")
    and os.geteuid() == 0
    and Path("/usr/bin/setpriv").is_file()
)
_ROOT_PROBE_ONLY = pytest.mark.skipif(
    not _ROOT_PROBE_AVAILABLE,
    reason="需要 Linux root、setpriv 与真实 venv",
)


def _run_protocol(runtime: TargetProbeRuntime) -> subprocess.CompletedProcess[bytes]:
    request = TargetProbeRequest(
        root=runtime.root,
        account=runtime.account,
        base_id=runtime.base.base_id,
        release_id=runtime.release.release_id,
    )
    layout = derive_probe_layout(request, runtime.base, runtime.release)
    command = build_probe_command(request, runtime.base, runtime.release, layout)
    argv = build_service_process_argv(
        runtime.account,
        command,
        probe_environment(layout),
    )
    return subprocess.run(
        argv,
        check=False,
        capture_output=True,
        cwd="/",
        env={},
        stdin=subprocess.DEVNULL,
        timeout=20,
    )


def _create_directory(
    path: Path,
    *,
    mode: int = 0o755,
    owner: int | None = None,
    package: str | None = None,
) -> Path:
    path.mkdir(parents=True)
    if package is not None:
        (path / package).mkdir()
    if owner is not None:
        os.chown(path, owner, -1)
    path.chmod(mode)
    return path


def _install_base_jieba_warnings(runtime: TargetProbeRuntime) -> None:
    package = _create_directory(runtime.base_purelib / "jieba")
    source = """import warnings
warnings.warn("第一条已知告警", SyntaxWarning)
warnings.warn("第二条已知告警", SyntaxWarning)
warnings.warn("第三条已知告警", SyntaxWarning)
warnings.warn("已知用户告警", UserWarning)
"""
    module = package / "__init__.py"
    module.write_text(source, encoding="utf-8")
    module.chmod(0o644)


def _inject_path_after_managed_import(
    runtime: TargetProbeRuntime,
    path: Path,
    *,
    position: Literal["append", "front", "before-base"] = "append",
) -> None:
    module = runtime.app_purelib / "codev_platform/chroma/server.py"
    operation = _path_insert_operation(runtime, position)
    module.write_text(
        f"import sys\npath = {os.fspath(path)!r}\nsys.path.{operation}\n",
        encoding="utf-8",
    )
    module.chmod(0o644)


def _path_insert_operation(
    runtime: TargetProbeRuntime,
    position: Literal["append", "front", "before-base"],
) -> str:
    if position == "front":
        return "insert(0, path)"
    if position == "before-base":
        return f"insert(sys.path.index({os.fspath(runtime.base_purelib)!r}), path)"
    return "append(path)"


@_ROOT_PROBE_ONLY
def test_真实目标用户允许后置只读base_vendor路径(tmp_path: Path) -> None:
    runtime = build_target_probe_runtime(tmp_path)
    vendor = _create_directory(runtime.base_purelib / "_vendor")
    _inject_path_after_managed_import(runtime, vendor)

    completed = _run_protocol(runtime)

    assert completed.returncode == 0
    assert completed.stdout == PROBE_SUCCESS_OUTPUT
    assert completed.stderr == b""


@_ROOT_PROBE_ONLY
def test_真实目标用户仅在导入期间提供探针项目标识(tmp_path: Path) -> None:
    runtime = build_target_probe_runtime(
        tmp_path,
        module_sources={
            "codev_platform.chroma.server": (
                "import os\n"
                "if os.environ.get('PLATFORM_PROJECT_ID') != 'codev-target-probe':\n"
                "    raise RuntimeError('探针项目标识缺失')\n"
            ),
        },
    )

    completed = _run_protocol(runtime)

    assert completed.returncode == 0
    assert completed.stdout == PROBE_SUCCESS_OUTPUT
    assert completed.stderr == b""


@_ROOT_PROBE_ONLY
def test_真实目标用户允许base_jieba已知导入告警(tmp_path: Path) -> None:
    runtime = build_target_probe_runtime(
        tmp_path,
        module_sources={"codev_platform.chroma.server": "import jieba\n"},
    )
    _install_base_jieba_warnings(runtime)

    completed = _run_protocol(runtime)

    assert completed.returncode == 0
    assert completed.stdout == PROBE_SUCCESS_OUTPUT
    assert completed.stderr == b""


@_ROOT_PROBE_ONLY
def test_真实目标用户拒绝应用模块产生的未知告警(tmp_path: Path) -> None:
    runtime = build_target_probe_runtime(
        tmp_path,
        module_sources={
            "codev_platform.chroma.server": (
                "import warnings\nwarnings.warn('应用模块未知告警', RuntimeWarning)\n"
            ),
        },
    )

    completed = _run_protocol(runtime)

    assert completed.returncode == 70
    assert completed.stdout == b""
    assert completed.stderr == b""


@_ROOT_PROBE_ONLY
@pytest.mark.parametrize(
    ("inside_base", "mode", "package", "position"),
    [
        (False, 0o755, None, "append"),
        (True, 0o775, None, "append"),
        (True, 0o755, None, "front"),
        (True, 0o755, None, "before-base"),
        (True, 0o755, "codev_platform", "append"),
        (True, 0o755, "torch", "append"),
    ],
    ids=[
        "外部路径",
        "组可写路径",
        "抢占应用优先级",
        "抢占基座优先级",
        "影子应用包",
        "影子torch包",
    ],
)
def test_真实目标用户拒绝不安全导入后路径(
    tmp_path: Path,
    inside_base: bool,
    mode: int,
    package: str | None,
    position: Literal["append", "front", "before-base"],
) -> None:
    runtime = build_target_probe_runtime(tmp_path)
    parent = runtime.base_purelib if inside_base else tmp_path
    candidate = _create_directory(parent / "unexpected-vendor", mode=mode, package=package)
    _inject_path_after_managed_import(runtime, candidate, position=position)

    completed = _run_protocol(runtime)

    assert completed.returncode == 70
    assert completed.stdout == b""
    assert completed.stderr == b""


@_ROOT_PROBE_ONLY
def test_真实目标用户拒绝非root所有的base路径(tmp_path: Path) -> None:
    runtime = build_target_probe_runtime(tmp_path)
    vendor = _create_directory(
        runtime.base_purelib / "unexpected-vendor",
        owner=runtime.account.uid,
    )
    _inject_path_after_managed_import(runtime, vendor)

    completed = _run_protocol(runtime)

    assert completed.returncode == 70
    assert completed.stdout == b""
    assert completed.stderr == b""
