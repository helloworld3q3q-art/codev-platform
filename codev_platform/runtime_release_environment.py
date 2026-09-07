"""薄 release 的 venv 构建、导入来源证明与动态一致性复验。"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from typing import TYPE_CHECKING

from codev_platform.core.runtime_models import BaseMetadata
from codev_platform.runtime_deadline import bounded_runtime_timeout
from codev_platform.runtime_errors import RuntimeBuildError
from codev_platform.runtime_process import isolated_process_environment
from codev_platform.runtime_release_paths import (
    absolute_reference as _absolute_reference,
    cwd_base_purelib_reference as _cwd_base_purelib_reference,
    managed_directory as _managed_directory,
    same_directory as _same_directory,
)

if TYPE_CHECKING:
    from codev_platform.runtime_wheel import WheelPayloadProof


MANAGED_IMPORTS = (
    "codev_platform.chroma.daemon_entry",
    "codev_platform.chroma.server",
    "codev_platform.codegraph.server",
    "codev_platform.graph.mcp_server",
    "codev_platform.agent.memory_mcp",
    "codev_platform.webhook.server",
    "codev_platform.agent.service",
    "codev_platform.web.app",
    "codev_platform.ops.memory_maintenance",
    "codev_platform.cli",
)
_SUBPROCESS_TIMEOUT_SEC = 600


@dataclass(frozen=True, slots=True)
class StagedEnvironment:
    python_relative: str
    purelib_relative: str
    base_link_relative: str
    base_pth_relative: str
    app_freeze_sha256: str


def stage_environment(
    release_dir: Path,
    wheel: Path,
    base_dir: Path,
    base: BaseMetadata,
    marker: Path,
    *,
    base_purelib_reference: Path | None = None,
    verify_runtime_binding: Callable[[], None] | None = None,
    finalize_base_pth: Callable[[Path, bytes], None] | None = None,
) -> StagedEnvironment:
    """在最终 release 目录创建薄 venv，并把依赖层绑定到只读基座。"""
    dynamic_cwd_reference = not release_dir.is_absolute() and os.name == "posix"
    if dynamic_cwd_reference and base_purelib_reference is None:
        raise RuntimeBuildError("薄 release 动态基座 pth 缺少命名基座发布引用")
    wheel_proof = _inspect_application_wheel(wheel)
    venv = release_dir / "venv"
    _run_checked((sys.executable, "-B", "-I", "-m", "venv", str(venv)))
    python = venv / "bin" / "python"
    if not python.is_file():
        raise RuntimeBuildError("薄 release Python 不可用")
    purelib = _managed_directory(
        venv,
        _venv_purelib_relative(python),
        error_message="薄 release purelib 不可用",
    )
    write_stage(marker, "after_venv")
    base_purelib = _managed_directory(
        base_dir,
        base.purelib_relative,
        error_message="依赖基座 purelib 不受信任",
    )
    base_link = release_dir / "base"
    base_target = Path("..") / ".." / "bases" / base.base_id
    base_link.symlink_to(base_target, target_is_directory=True)
    try:
        base_link_target = os.readlink(base_link)
    except OSError:
        raise RuntimeBuildError("薄 release 基座相对链接不可读") from None
    if base_link_target != os.fspath(base_target) or not _same_directory(base_link, base_dir):
        raise RuntimeBuildError("薄 release 基座相对链接目标不一致")
    pth = purelib / "codev_platform_base.pth"
    final_reference = _absolute_reference(
        base_purelib if base_purelib_reference is None else base_purelib_reference,
        error_message="依赖基座 purelib 发布引用不可用",
    )
    if dynamic_cwd_reference:
        staging_reference = _absolute_reference(
            _cwd_base_purelib_reference(base.base_id, base.purelib_relative),
            error_message="依赖基座动态 purelib 发布引用不可用",
        )
        _require_dynamic_pth_guards(
            final_reference,
            verify_runtime_binding,
            finalize_base_pth,
        )
    else:
        staging_reference = final_reference
    write_bytes_exclusive(
        pth,
        f"{staging_reference.as_posix()}\n".encode(),
        mode=0o640,
    )
    _verify_execution_trust(release_dir.parent.parent, release_dir, python, purelib)
    _verify_runtime_binding(verify_runtime_binding)
    _run_checked(
        (
            str(python),
            "-B",
            "-I",
            "-m",
            "pip",
            "--isolated",
            "--disable-pip-version-check",
            "--no-input",
            "install",
            "--no-index",
            "--no-deps",
            "--no-compile",
            str(wheel),
        )
    )
    _verify_installed_application(wheel_proof, purelib, pth)
    _verify_runtime_binding(verify_runtime_binding)
    freeze = _run_deep_probes(python, purelib, base_purelib)
    _verify_installed_application(wheel_proof, purelib, pth)
    _verify_runtime_binding(verify_runtime_binding)
    if staging_reference != final_reference:
        _verify_runtime_binding(verify_runtime_binding)
        _finalize_base_pth(
            pth,
            f"{final_reference.as_posix()}\n".encode(),
            finalize_base_pth,
        )
        _verify_installed_application(wheel_proof, purelib, pth)
        _verify_runtime_binding(verify_runtime_binding)
    return StagedEnvironment(
        python_relative=python.relative_to(release_dir).as_posix(),
        purelib_relative=purelib.relative_to(release_dir).as_posix(),
        base_link_relative="base",
        base_pth_relative=pth.relative_to(release_dir).as_posix(),
        app_freeze_sha256=hashlib.sha256(freeze.encode()).hexdigest(),
    )


def _venv_purelib_relative(python: Path) -> str:
    """只接收目标解释器相对自身 venv 的 purelib 后代。"""
    purelib_text = _run_checked(
        (
            str(python),
            "-B",
            "-I",
            "-c",
            "from pathlib import Path; import sys,sysconfig; "
            "purelib=Path(sysconfig.get_paths()['purelib']); "
            "print(purelib.relative_to(sys.prefix).as_posix())",
        )
    ).strip()
    return purelib_text


def _verify_runtime_binding(checker: Callable[[], None] | None) -> None:
    """在动态解释器边界前后复验 root-fd binding 仍指向可见根。"""
    if checker is not None:
        checker()


def _require_dynamic_pth_guards(
    final_reference: Path,
    checker: Callable[[], None] | None,
    finalizer: Callable[[Path, bytes], None] | None,
) -> None:
    """root-fd 动态导入只能先经 cwd，随后受控切换到命名发布引用。"""
    process_reference = Path("/proc/self")
    if final_reference.is_relative_to(process_reference):
        raise RuntimeBuildError("薄 release 动态基座 pth 不得作为激活引用")
    if checker is None:
        raise RuntimeBuildError("薄 release 动态基座 pth 缺少绑定复验")
    if finalizer is None:
        raise RuntimeBuildError("薄 release 动态基座 pth 缺少受控最终发布器")


def _finalize_base_pth(
    path: Path,
    content: bytes,
    finalizer: Callable[[Path, bytes], None] | None,
) -> None:
    """仅由 root-fd worker 注入的原子发布器切换动态 `.pth` 到最终引用。"""
    if finalizer is None:
        raise RuntimeBuildError("薄 release 动态基座 pth 缺少受控最终发布器")
    finalizer(path, content)


def verify_environment(
    runtime_root: Path,
    release_dir: Path,
    python: Path,
    purelib: Path,
    base_purelib: Path,
    wheel: Path,
    expected_freeze_sha256: str,
    *,
    installed_application_verifier: Callable[[WheelPayloadProof, Path, Path], None] | None = None,
) -> None:
    """只做静态载荷和执行路径证明，不执行目标 Python。"""
    _verify_execution_trust(runtime_root, release_dir, python, purelib)
    wheel_proof = _inspect_application_wheel(wheel)
    verifier = (
        _verify_installed_application
        if installed_application_verifier is None
        else installed_application_verifier
    )
    verifier(
        wheel_proof,
        purelib,
        purelib / "codev_platform_base.pth",
    )
    if not base_purelib.is_dir() or base_purelib.is_symlink():
        raise RuntimeBuildError("薄 release 基座 purelib 不受信任")


def deep_verify_environment(
    runtime_root: Path,
    release_dir: Path,
    python: Path,
    purelib: Path,
    base_purelib: Path,
    wheel: Path,
    expected_freeze_sha256: str,
) -> None:
    """显式执行一次深探针，并用两次静态证明包围动态边界。"""
    verify_environment(
        runtime_root,
        release_dir,
        python,
        purelib,
        base_purelib,
        wheel,
        expected_freeze_sha256,
    )
    freeze = _run_deep_probes(python, purelib, base_purelib)
    if hashlib.sha256(freeze.encode()).hexdigest() != expected_freeze_sha256:
        raise RuntimeBuildError("薄 release 应用 freeze 摘要漂移")
    verify_environment(
        runtime_root,
        release_dir,
        python,
        purelib,
        base_purelib,
        wheel,
        expected_freeze_sha256,
    )


def _run_deep_probes(python: Path, purelib: Path, base_purelib: Path) -> str:
    _run_checked((str(python), "-B", "-I", "-m", "pip", "--isolated", "check"))
    probe_release_imports(python, purelib, base_purelib)
    return _run_checked((str(python), "-B", "-I", "-m", "pip", "--isolated", "freeze", "--all"))


def read_runtime_identity(python: Path, metadata_file: Path) -> dict[str, object]:
    """由目标 Python 返回邻接 release 身份；调用者仍负责逐字段比对。"""
    payload = _run_checked(
        (
            str(python),
            "-B",
            "-I",
            "-c",
            "import json; from codev_platform.core.runtime_identity import runtime_identity; print(json.dumps(runtime_identity().as_dict(), sort_keys=True))",
        ),
        env={"CODEV_PLATFORM_RELEASE_FILE": str(metadata_file)},
    )
    try:
        identity = json.loads(payload)
    except json.JSONDecodeError as error:
        raise RuntimeBuildError("薄 release 子进程身份输出无效") from error
    if type(identity) is not dict:
        raise RuntimeBuildError("薄 release 子进程身份输出无效")
    return identity


def probe_release_imports(python: Path, purelib: Path, base_purelib: Path) -> None:
    modules = repr(MANAGED_IMPORTS)
    script = (
        "from pathlib import Path; import importlib,sys,torch; "
        "app=Path(sys.argv[1]).resolve(); base=Path(sys.argv[2]).resolve(); "
        "paths=[Path(p).resolve() for p in sys.path if p]; "
        "assert app in paths and base in paths and paths.index(app)<paths.index(base); "
        f"loaded=[importlib.import_module(x) for x in {modules}]; "
        "assert all(Path(m.__file__).resolve().is_relative_to(app) for m in loaded); "
        "assert Path(torch.__file__).resolve().is_relative_to(base); print('ok')"
    )
    output = _run_checked((str(python), "-B", "-I", "-c", script, str(purelib), str(base_purelib)))
    if output.strip() != "ok":
        raise RuntimeBuildError("薄 release 导入来源无法证明")


def _verify_execution_trust(
    root: Path,
    release_dir: Path,
    python: Path,
    purelib: Path,
) -> None:
    from codev_platform.runtime_execution_trust import (
        RuntimeExecutionTrustError,
        verify_execution_trust,
        verify_execution_trust_from_cwd,
    )

    try:
        if root.is_absolute():
            verify_execution_trust(root, release_dir, ((python, True), (purelib, False)))
        else:
            verify_execution_trust_from_cwd(
                release_dir,
                ((python, True), (purelib, False)),
            )
    except RuntimeExecutionTrustError as error:
        raise RuntimeBuildError(str(error)) from None


def _inspect_application_wheel(wheel: Path) -> WheelPayloadProof:
    from codev_platform.runtime_wheel import RuntimeWheelError, inspect_application_wheel

    try:
        return inspect_application_wheel(wheel)
    except RuntimeWheelError as error:
        raise RuntimeBuildError(str(error)) from None


def _verify_installed_application(
    proof: WheelPayloadProof,
    purelib: Path,
    base_pth: Path,
) -> None:
    from codev_platform.runtime_wheel import RuntimeWheelError, verify_installed_application

    try:
        verify_installed_application(proof, purelib, base_pth)
    except RuntimeWheelError as error:
        raise RuntimeBuildError(str(error)) from None


def _run_checked(
    command: tuple[str, ...],
    *,
    timeout: int = _SUBPROCESS_TIMEOUT_SEC,
    env: dict[str, str] | None = None,
) -> str:
    process_env = isolated_process_environment(overrides=env)
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=True,
            timeout=bounded_runtime_timeout(timeout),
            env=process_env,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeBuildError("运行时构建子进程失败") from error
    return completed.stdout


def write_bytes_exclusive(path: Path, content: bytes, *, mode: int) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
        mode,
    )
    try:
        if os.name != "nt":
            os.fchmod(descriptor, mode)
        write_all(descriptor, content)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise RuntimeBuildError("运行时制品写入未取得进展")
        offset += written


def write_stage(marker: Path, stage: str) -> None:
    allowed = {"after_marker", "after_venv", "after_install", "after_release_json"}
    if stage not in allowed:
        raise RuntimeBuildError("薄 release 完成阶段无效")
    mode = "xb" if not marker.exists() else "r+b"
    with marker.open(mode) as stream:
        stream.seek(0)
        stream.write((stage + "\n").encode("ascii"))
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())


__all__ = [
    "MANAGED_IMPORTS",
    "StagedEnvironment",
    "deep_verify_environment",
    "probe_release_imports",
    "read_runtime_identity",
    "stage_environment",
    "verify_environment",
    "write_bytes_exclusive",
    "write_stage",
]
