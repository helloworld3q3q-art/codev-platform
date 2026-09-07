"""从已验证版本、可编辑源码或已安装发行包发现唯一运行时身份。"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

from .runtime_models import (
    RuntimeIdentity,
    RuntimeModelError,
)
from .runtime_release_identity import release_identity


class RuntimeIdentityError(RuntimeError):
    """声明的运行时来源无法形成可信身份。"""


def _error(message: str) -> RuntimeIdentityError:
    return RuntimeIdentityError(message)


def _process_paths() -> tuple[str, str]:
    return str(Path(sys.executable).resolve()), str(Path(sys.prefix).resolve())


def _editable_root(direct_url_text: str) -> Path | None:
    try:
        payload = json.loads(direct_url_text)
    except (TypeError, json.JSONDecodeError):
        raise _error("PEP 610 元数据无效") from None
    if type(payload) is not dict:
        raise _error("PEP 610 元数据无效")
    directory_info = payload.get("dir_info")
    if directory_info is None:
        return None
    if type(directory_info) is not dict:
        raise _error("PEP 610 目录声明无效")
    if directory_info.get("editable") is not True:
        return None
    raw_url = payload.get("url")
    if type(raw_url) is not str:
        raise _error("PEP 610 可编辑地址无效")
    parsed = urlsplit(raw_url)
    if (
        parsed.scheme != "file"
        or parsed.hostname not in {None, "", "localhost"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise _error("PEP 610 可编辑地址不是本地文件")
    try:
        native_path = Path(url2pathname(parsed.path))
    except (OSError, ValueError):
        raise _error("PEP 610 可编辑地址无法解码") from None
    if (
        not parsed.path
        or not native_path.is_absolute()
        or native_path.drive.startswith("\\\\")
    ):
        raise _error("PEP 610 可编辑地址必须是绝对路径")
    try:
        root = native_path.resolve(strict=True)
    except OSError:
        raise _error("PEP 610 可编辑源码目录不可用") from None
    if not root.is_dir():
        raise _error("PEP 610 可编辑源码目录不可用")
    return root


def _editable_identity(source_root: Path) -> RuntimeIdentity:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD^{commit}"],
            cwd=source_root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=True,
            timeout=3,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        raise _error("可编辑源码的 Git 身份不可用") from None
    lines = completed.stdout.splitlines() if type(completed.stdout) is str else []
    if len(lines) != 1 or lines[0] != lines[0].strip():
        raise _error("可编辑源码的 Git 身份输出无效")
    interpreter, prefix = _process_paths()
    try:
        return RuntimeIdentity(
            mode="editable",
            runtime_revision=lines[0],
            release_id=None,
            wheel_sha256=None,
            base_id=None,
            base_requirements_sha256=None,
            interpreter_realpath=interpreter,
            environment_prefix=prefix,
            source_root=str(source_root),
        )
    except RuntimeModelError:
        raise _error("可编辑源码的 Git 身份无效") from None


def _installed_identity(distribution: metadata.Distribution) -> RuntimeIdentity:
    records = [entry for entry in (distribution.files or ())
        if Path(str(entry)).name == "RECORD"
        and Path(str(entry)).parent.name.endswith(".dist-info")
    ]
    if len(records) != 1:
        raise _error("已安装发行包的 RECORD 不唯一")
    version = distribution.version
    if type(version) is not str or not version:
        raise _error("已安装发行包版本无效")
    try:
        record = Path(distribution.locate_file(records[0])).read_bytes()
    except OSError:
        raise _error("已安装发行包的 RECORD 不可读") from None
    digest = hashlib.sha256(version.encode("utf-8") + b"\0" + record).hexdigest()
    interpreter, prefix = _process_paths()
    return RuntimeIdentity(
        mode="installed",
        runtime_revision=digest,
        release_id=None,
        wheel_sha256=None,
        base_id=None,
        base_requirements_sha256=None,
        interpreter_realpath=interpreter,
        environment_prefix=prefix,
        source_root=None,
    )


def _discover_identity() -> RuntimeIdentity:
    declared = os.environ.get("CODEV_PLATFORM_RELEASE_FILE")
    if declared:
        return release_identity(Path(declared))
    adjacent = Path(sys.prefix).parent / "release.json"
    if adjacent.is_file():
        return release_identity(adjacent)
    distribution = metadata.distribution("codev-platform")
    direct_url = distribution.read_text("direct_url.json")
    if direct_url is not None:
        source_root = _editable_root(direct_url)
        if source_root is not None:
            return _editable_identity(source_root)
    return _installed_identity(distribution)


@lru_cache(maxsize=1)
def runtime_identity() -> RuntimeIdentity:
    """首次发现并缓存本进程不可变的运行时身份。"""
    try:
        return _discover_identity()
    except RuntimeIdentityError:
        raise
    except Exception:  # noqa: BLE001 - 对外只暴露固定且无底层 cause 的 readiness 失败类型
        raise _error("运行时身份不可验证") from None
