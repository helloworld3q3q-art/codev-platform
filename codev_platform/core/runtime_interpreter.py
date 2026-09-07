"""从已证明运行身份选择稳定解释器，同时保留 venv 启动语义。"""

from __future__ import annotations

import hmac
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath

from codev_platform.core.runtime_models import (
    RuntimeIdentity,
    require_runtime_revision,
    require_sha256,
)

REINDEX_RUNTIME_REVISION_ENV = "CODEV_REINDEX_EXPECTED_RUNTIME_REVISION"
_RUNTIME_REVISION_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


@dataclass(frozen=True, slots=True)
class ReleaseInterpreterIdentity:
    """发布模式下绑定源码版本、制品身份与词法解释器的窄快照。"""

    runtime_revision: str
    release_id: str
    interpreter_path: str

    def __post_init__(self) -> None:
        revision = require_runtime_revision(self.runtime_revision, git_only=True)
        release_id = require_sha256(self.release_id, field="release_id")
        interpreter = _require_posix_interpreter_path(self.interpreter_path)
        object.__setattr__(self, "runtime_revision", revision)
        object.__setattr__(self, "release_id", release_id)
        object.__setattr__(self, "interpreter_path", interpreter)


def _require_posix_interpreter_path(value: object) -> str:
    if type(value) is not str or not value or value.startswith("//"):
        raise ValueError("发布解释器路径无效")
    path = PurePosixPath(value)
    if (
        not path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts[1:])
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
        or any(char in value for char in ("'", '"', "\\"))
    ):
        raise ValueError("发布解释器路径无效")
    return value


def proven_interpreter_path(
    identity: RuntimeIdentity,
    *,
    executable: str | Path | None = None,
    process_prefix: str | Path | None = None,
) -> Path:
    """把当前解释器的 venv 内相对位置固定到身份中的不可变环境。

    只解析父目录，不解析 ``bin/python`` 最后一跳。POSIX venv 的 Python 常是
    指向系统解释器的符号链接；直接 ``resolve()`` 会丢失 ``pyvenv.cfg`` 与依赖环境。
    """
    if not isinstance(identity, RuntimeIdentity):
        raise ValueError("运行身份类型无效")
    current = Path(executable if executable is not None else sys.executable)
    current_prefix = Path(process_prefix if process_prefix is not None else sys.prefix)
    try:
        relative = current.relative_to(current_prefix)
    except ValueError:
        raise ValueError("当前解释器不属于进程环境") from None
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("当前解释器相对路径无效")

    prefix = Path(identity.environment_prefix)
    try:
        canonical_prefix = prefix.resolve(strict=True)
    except OSError:
        raise ValueError("已证明解释器环境不可用") from None
    if not prefix.is_absolute() or prefix != canonical_prefix or not prefix.is_dir():
        raise ValueError("已证明解释器环境不是规范目录")

    candidate = prefix / relative
    try:
        logical = candidate.parent.resolve(strict=True) / candidate.name
        actual_target = logical.resolve(strict=True)
        expected_target = Path(identity.interpreter_realpath).resolve(strict=True)
    except OSError:
        raise ValueError("已证明解释器不可用") from None
    if (
        not logical.is_relative_to(prefix)
        or not logical.is_file()
        or actual_target != expected_target
    ):
        raise ValueError("已证明解释器与运行身份不一致")
    return logical


def current_proven_interpreter_path() -> Path:
    """返回当前已验证运行身份的词法解释器路径，不折叠 venv 最后一跳。"""
    from codev_platform.core.runtime_identity import runtime_identity

    return proven_interpreter_path(runtime_identity())


def current_release_interpreter_identity() -> ReleaseInterpreterIdentity:
    """从当前进程一次性取得仅发布模式可用的完整服务解释器身份。"""
    from codev_platform.core.runtime_identity import runtime_identity

    identity = runtime_identity()
    if (
        type(identity) is not RuntimeIdentity
        or identity.mode != "release"
        or identity.release_id is None
    ):
        raise ValueError("systemd 发布门禁只接受 release 运行模式")
    interpreter = proven_interpreter_path(identity)
    return ReleaseInterpreterIdentity(
        runtime_revision=identity.runtime_revision,
        release_id=identity.release_id,
        interpreter_path=interpreter.as_posix(),
    )


def verify_reindex_runtime_revision(expected: str | None = None) -> str | None:
    """跨 exec 复核运行版本；legacy 未提供任何声明时保持无操作。"""
    environment_value = os.environ.get(REINDEX_RUNTIME_REVISION_ENV)
    if expected is None and environment_value is None:
        return None
    if expected is not None and environment_value != expected:
        raise RuntimeError("隔离 reindex 运行版本环境绑定不一致")
    selected = expected if expected is not None else environment_value
    if (
        type(selected) is not str
        or _RUNTIME_REVISION_RE.fullmatch(selected) is None
        or set(selected) == {"0"}
    ):
        raise RuntimeError("隔离 reindex 运行版本声明无效")
    from codev_platform.core.runtime_identity import runtime_identity

    actual = runtime_identity().runtime_revision
    if not hmac.compare_digest(actual, selected):
        raise RuntimeError("隔离 reindex 运行版本发生漂移")
    return actual


__all__ = [
    "ReleaseInterpreterIdentity",
    "REINDEX_RUNTIME_REVISION_ENV",
    "current_release_interpreter_identity",
    "current_proven_interpreter_path",
    "proven_interpreter_path",
    "verify_reindex_runtime_revision",
]
