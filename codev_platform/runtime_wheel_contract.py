"""应用 wheel 静态证明的稳定数据契约。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class RuntimeWheelError(RuntimeError):
    """wheel 或已安装应用源码不能形成可信静态证明。"""


@dataclass(frozen=True, slots=True)
class PayloadFile:
    """wheel 中一个受管应用文件的不可变证明。"""

    archive_name: str
    installed_relative: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class WheelPayloadProof:
    """pip 执行前形成的不可变应用 wheel 载荷证明。"""

    wheel_path: Path
    wheel_sha256: str
    dist_info_relative: str
    application_files: tuple[PayloadFile, ...]


__all__ = ["PayloadFile", "RuntimeWheelError", "WheelPayloadProof"]
