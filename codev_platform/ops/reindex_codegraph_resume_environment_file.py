"""CodeGraph 受控恢复所需环境文件的兼容可信读取入口。"""
from __future__ import annotations

from pathlib import Path

from codev_platform.ops.reindex_codegraph_resume_managed_path import (
    TrustedManagedPathError,
    read_optional_root_owned_regular_file,
)


_MAX_ENVIRONMENT_FILE_BYTES = 128 * 1024


class TrustedEnvironmentFileError(RuntimeError):
    """环境文件或其祖先目录无法证明为 root 受控。"""


def read_trusted_environment_file(path: Path) -> bytes:
    """保留环境文件专用错误契约，底层统一走 dirfd 可信路径叶子。"""
    _require_lexical_absolute_path(path)
    try:
        content = read_optional_root_owned_regular_file(
            path,
            max_bytes=_MAX_ENVIRONMENT_FILE_BYTES,
        )
    except TrustedManagedPathError as error:
        raise TrustedEnvironmentFileError("受管服务环境文件不受信任") from error
    if content is None:
        raise TrustedEnvironmentFileError("受管服务环境文件不受信任")
    return content


def _require_lexical_absolute_path(value: Path) -> None:
    try:
        path = Path(value)
        components = tuple(str(item) for item in path.parts[1:])
        if not path.is_absolute() or not components or any(
            item in {"", ".", ".."} for item in components
        ):
            raise TrustedEnvironmentFileError("受管服务环境文件不受信任")
    except TrustedEnvironmentFileError:
        raise
    except (TypeError, ValueError):
        raise TrustedEnvironmentFileError("受管服务环境文件不受信任") from None


__all__ = ["TrustedEnvironmentFileError", "read_trusted_environment_file"]
