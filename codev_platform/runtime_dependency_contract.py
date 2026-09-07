"""运行时依赖锁与已安装发行包共享的稳定契约。"""

from __future__ import annotations

from dataclasses import dataclass
import re

from codev_platform.core.runtime_models import RequirementsLockInfo


REQUIRED_RUNTIME_DISTRIBUTIONS = frozenset(
    {
        "alembic",
        "anthropic",
        "chromadb",
        "cryptography",
        "fastapi",
        "httptools",
        "jieba",
        "mcp",
        "mcp-proxy",
        "openai",
        "psycopg",
        "psycopg-binary",
        "psycopg-pool",
        "pydantic",
        "rank-bm25",
        "sentence-transformers",
        "sqlalchemy",
        "torch",
        "uvicorn",
        "watchfiles",
        "websockets",
    }
)

RUNTIME_IMPORT_MODULES = (
    "torch",
    "chromadb",
    "sentence_transformers",
    "rank_bm25",
    "jieba",
    "watchfiles",
    "websockets",
    "httptools",
    "cryptography",
    "fastapi",
    "uvicorn",
    "anthropic",
    "openai",
    "pydantic",
    "mcp",
    "mcp_proxy",
    "psycopg",
    "psycopg_pool",
    "sqlalchemy",
    "alembic",
)


@dataclass(frozen=True, slots=True)
class DistributionPin:
    """一个规范发行包及其精确版本。"""

    name: str
    version: str

    @property
    def requirement(self) -> str:
        return f"{self.name}=={self.version}"


@dataclass(frozen=True, slots=True)
class LockedWheelArtifact:
    """hash lock 绑定的单个 Linux wheel 制品。"""

    package: str
    filename: str
    sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.package) is not str
            or not self.package
            or type(self.filename) is not str
            or not self.filename.endswith(".whl")
            or type(self.sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None
        ):
            raise ValueError("依赖锁 wheel 制品身份无效")


@dataclass(frozen=True, slots=True)
class RequirementsLockContract:
    """一次解析同时产出的锁身份与完整 pin 集合。"""

    info: RequirementsLockInfo
    pins: tuple[DistributionPin, ...]
    artifacts: tuple[LockedWheelArtifact, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.info, RequirementsLockInfo):
            raise TypeError("依赖锁身份类型无效")
        if type(self.pins) is not tuple or not self.pins:
            raise TypeError("依赖锁 pin 集合无效")
        if any(type(pin) is not DistributionPin for pin in self.pins):
            raise TypeError("依赖锁 pin 类型无效")
        names = tuple(pin.name for pin in self.pins)
        if names != tuple(sorted(names)) or len(set(names)) != len(names):
            raise ValueError("依赖锁 pin 必须按规范名称唯一排序")
        if len(self.pins) != self.info.pin_count:
            raise ValueError("依赖锁 pin 数量与身份不一致")
        if (
            type(self.artifacts) is not tuple
            or any(type(item) is not LockedWheelArtifact for item in self.artifacts)
        ):
            raise TypeError("依赖锁 wheel 制品集合无效")
        filenames = tuple(item.filename for item in self.artifacts)
        packages = tuple(item.package for item in self.artifacts)
        if (
            filenames != tuple(sorted(filenames))
            or len(set(filenames)) != len(filenames)
            or set(packages) != set(names)
            or len(packages) != len(names)
        ):
            raise ValueError("依赖锁 wheel 制品必须与 pin 一一对应并按文件名排序")


__all__ = [
    "DistributionPin",
    "LockedWheelArtifact",
    "REQUIRED_RUNTIME_DISTRIBUTIONS",
    "RUNTIME_IMPORT_MODULES",
    "RequirementsLockContract",
]
