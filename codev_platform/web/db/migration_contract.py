"""数据库迁移协调器的不可变声明、端口与无秘密结果。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
import re


_REVISION = re.compile(r"[a-z0-9_]{1,64}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class DatabaseMigrationError(RuntimeError):
    """数据库迁移无法在可证明的事务边界内完成。"""


@dataclass(frozen=True, slots=True)
class SchemaFingerprint:
    """固定受管表集合的目录指纹。"""

    digest: str
    table_count: int

    def __post_init__(self) -> None:
        if type(self.digest) is not str or _SHA256.fullmatch(self.digest) is None:
            raise DatabaseMigrationError("数据库 schema 指纹无效")
        if type(self.table_count) is not int or self.table_count < 0:
            raise DatabaseMigrationError("数据库 schema 表计数无效")


@dataclass(frozen=True, slots=True)
class RevisionState:
    """目标 schema 中 Alembic 版本表的精确状态。"""

    table_present: bool
    revision: str | None

    def __post_init__(self) -> None:
        if type(self.table_present) is not bool:
            raise DatabaseMigrationError("数据库版本状态无效")
        if self.table_present:
            _require_revision(self.revision)
        elif self.revision is not None:
            raise DatabaseMigrationError("数据库版本状态无效")


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """不可变 release 内的单线 Alembic revision 链。"""

    revisions: tuple[str, ...]
    head: str

    def __post_init__(self) -> None:
        try:
            revisions = tuple(self.revisions)
        except TypeError:
            raise DatabaseMigrationError("数据库迁移链无效") from None
        if (
            not revisions
            or any(_require_revision(item) != item for item in revisions)
            or len(revisions) != len(set(revisions))
            or _require_revision(self.head) != self.head
            or revisions[-1] != self.head
        ):
            raise DatabaseMigrationError("数据库迁移链无效")
        object.__setattr__(self, "revisions", revisions)


@dataclass(frozen=True, slots=True)
class MigrationReport:
    """迁移完成后的无凭据报告。"""

    previous_revision: str | None
    adopted_revision: str | None
    head_revision: str
    legacy_adopted: bool


@dataclass(frozen=True, slots=True)
class DatabaseVerificationReport:
    """迁移后独立只读复验的无凭据报告。"""

    head_revision: str
    schema_sha256: str
    table_count: int

    def __post_init__(self) -> None:
        _require_revision(self.head_revision)
        if type(self.schema_sha256) is not str or _SHA256.fullmatch(self.schema_sha256) is None:
            raise DatabaseMigrationError("数据库验收指纹无效")
        if type(self.table_count) is not int or self.table_count < 0:
            raise DatabaseMigrationError("数据库验收表计数无效")


@dataclass(frozen=True, slots=True)
class DatabaseMigrationPorts:
    """协调器依赖的 PostgreSQL/Alembic 适配端口。"""

    transaction: Callable[[], AbstractContextManager[None]]
    acquire_lock: Callable[[], None]
    read_revision: Callable[[], RevisionState]
    read_fingerprint: Callable[[], SchemaFingerprint]
    build_reference_fingerprints: Callable[
        [tuple[str, ...]], Mapping[str, SchemaFingerprint]
    ]
    stamp_revision: Callable[[str], None]
    upgrade_revision: Callable[[str], None]
    verify_runtime_contract: Callable[[], None]

    def validate(self) -> DatabaseMigrationPorts:
        if type(self) is not DatabaseMigrationPorts or not all(
            callable(value)
            for value in (
                self.transaction,
                self.acquire_lock,
                self.read_revision,
                self.read_fingerprint,
                self.build_reference_fingerprints,
                self.stamp_revision,
                self.upgrade_revision,
                self.verify_runtime_contract,
            )
        ):
            raise DatabaseMigrationError("数据库迁移端口无效")
        return self


@dataclass(frozen=True, slots=True)
class DatabaseVerificationPorts:
    """迁移后验收只允许只读事务和三个只读证明。"""

    readonly_transaction: Callable[[], AbstractContextManager[None]]
    read_revision: Callable[[], RevisionState]
    read_fingerprint: Callable[[], SchemaFingerprint]
    verify_runtime_contract: Callable[[], None]

    def validate(self) -> DatabaseVerificationPorts:
        if type(self) is not DatabaseVerificationPorts or not all(
            callable(value)
            for value in (
                self.readonly_transaction,
                self.read_revision,
                self.read_fingerprint,
                self.verify_runtime_contract,
            )
        ):
            raise DatabaseMigrationError("数据库验收端口无效")
        return self


def _require_revision(value: object) -> str:
    if type(value) is not str or _REVISION.fullmatch(value) is None:
        raise DatabaseMigrationError("数据库 revision 无效")
    return value


__all__ = [
    "DatabaseMigrationError",
    "DatabaseMigrationPorts",
    "DatabaseVerificationPorts",
    "DatabaseVerificationReport",
    "MigrationPlan",
    "MigrationReport",
    "RevisionState",
    "SchemaFingerprint",
]
