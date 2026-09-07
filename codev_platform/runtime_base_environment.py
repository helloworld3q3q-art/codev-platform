"""依赖基座虚拟环境创建与隔离子进程适配器。"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
import venv

from codev_platform.runtime_deadline import bounded_runtime_timeout
from codev_platform.runtime_process import isolated_process_environment


@dataclass(frozen=True, slots=True)
class VenvLayout:
    """基座虚拟环境内三个受管路径。"""

    python: Path
    purelib: Path
    bin_dir: Path


def create_venv(base_dir: Path) -> VenvLayout:
    environment = base_dir / "venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    bin_name = "Scripts" if os.name == "nt" else "bin"
    python_name = "python.exe" if os.name == "nt" else "python"
    python = environment / bin_name / python_name
    if os.name == "nt":
        purelib = environment / "Lib" / "site-packages"
    else:
        version = f"python{sys.version_info.major}.{sys.version_info.minor}"
        purelib = environment / "lib" / version / "site-packages"
    return VenvLayout(python=python, purelib=purelib, bin_dir=environment / bin_name)


def isolated_arguments(arguments: tuple[str, ...]) -> tuple[str, ...]:
    remaining = list(arguments)
    while remaining and remaining[0] in {"-B", "-I"}:
        remaining.pop(0)
    return ("-B", "-I", *remaining)


def run_python(python: Path, arguments: tuple[str, ...]) -> bytes:
    normalized = isolated_arguments(arguments)
    network = "install" in normalized and "--no-index" not in normalized
    completed = subprocess.run(
        (str(python), *normalized),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        timeout=bounded_runtime_timeout(3600.0),
        env=isolated_process_environment(network=network),
    )
    return completed.stdout


__all__ = ["VenvLayout", "create_venv", "isolated_arguments", "run_python"]
