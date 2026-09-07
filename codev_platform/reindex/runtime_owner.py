"""隔离 worker 跨重启 queue owner 的独立耐久身份。"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from codev_platform.core.paths import data_root

from .file_advisory_lock import advisory_lock
from .file_durability import durable_write_replace, read_regular_file_bounded

_SCHEMA_VERSION = 1
_MAX_BYTES = 16 * 1024
_LOCK_TIMEOUT_SEC = 5.0
_OWNER_NAME = "reindex-queue-owner.json"
_KIND_RE = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_HEX_RE = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN_RE = re.compile(r"[0-9a-f]{32}\Z")


class QueueOwnerError(RuntimeError):
    """queue owner 读取、写入或恢复门禁失败。"""


class QueueOwnerRecoveryRequired(QueueOwnerError):
    """存在待恢复状态时不允许生成或轮换 queue owner。"""


class QueueOwnerBootstrapRequired(QueueOwnerError):
    """正常 worker 缺少显式初始化过的 queue owner。"""


class QueueOwnerCorruptionError(QueueOwnerError):
    """queue owner 文件不符合严格 schema。"""


class QueueOwnerUnavailableError(QueueOwnerError):
    """无法安全读取 queue owner 文件。"""


class QueueOwnerTimeout(QueueOwnerError):
    """在有限预算内无法取得 queue owner 锁。"""


@dataclass(frozen=True, slots=True)
class QueueBackendBinding:
    """不含原始定位信息的队列后端绑定。"""

    kind: str
    fingerprint: str

    def __post_init__(self) -> None:
        if type(self.kind) is not str or _KIND_RE.fullmatch(self.kind) is None:
            raise ValueError("queue 后端类型无效")
        if type(self.fingerprint) is not str or _HEX_RE.fullmatch(self.fingerprint) is None:
            raise ValueError("queue 后端指纹无效")


@dataclass(frozen=True, slots=True)
class QueueOwnerIdentity:
    """只向生产组合根暴露稳定 token 与无秘密绑定。"""

    token: str = field(repr=False)
    binding: QueueBackendBinding
    created_at: float

    def __post_init__(self) -> None:
        if type(self.token) is not str or _TOKEN_RE.fullmatch(self.token) is None:
            raise ValueError("queue owner token 无效")
        if type(self.binding) is not QueueBackendBinding:
            raise ValueError("queue owner binding 无效")
        if type(self.created_at) not in (int, float) or not math.isfinite(float(self.created_at)):
            raise ValueError("queue owner created_at 无效")
        if self.created_at < 0:
            raise ValueError("queue owner created_at 不能为负数")


@dataclass(frozen=True, slots=True)
class _Deadline:
    expires_at: float

    @classmethod
    def start(cls, timeout_sec: float) -> _Deadline:
        if type(timeout_sec) not in (int, float) or not math.isfinite(float(timeout_sec)):
            raise ValueError("queue owner 锁超时必须是有限正数")
        if timeout_sec <= 0:
            raise ValueError("queue owner 锁超时必须是有限正数")
        return cls(time.monotonic() + float(timeout_sec))

    def remaining(self) -> float:
        return max(0.0, self.expires_at - time.monotonic())

    def check(self) -> None:
        if self.remaining() <= 0:
            raise QueueOwnerTimeout("queue owner 锁等待超时")


def fingerprint_backend(kind: str, locator: str) -> str:
    """对后端种类与定位信息做单向、域分隔的稳定摘要。"""
    if type(kind) is not str or _KIND_RE.fullmatch(kind) is None:
        raise ValueError("queue 后端类型无效")
    if type(locator) is not str or not locator or "\x00" in locator:
        raise ValueError("queue 后端定位信息无效")
    payload = f"codev-platform/reindex-queue/{kind}\x00{locator}".encode()
    return hashlib.sha256(payload).hexdigest()


def queue_owner_path(*, create_parent: bool = True) -> Path:
    """返回唯一受管的稳定 queue owner 路径。"""
    root = data_root() / "run"
    if create_parent:
        root.mkdir(parents=True, exist_ok=True)
    return root / _OWNER_NAME


def _pairs_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("queue owner JSON 包含重复键")
        value[key] = item
    return value


def _reject_constant(_value: str) -> object:
    raise ValueError("queue owner JSON 不允许非有限数")


def read_queue_owner_file(path: Path) -> QueueOwnerIdentity | None:
    """只读解析给定绝对路径的稳定 queue owner 文件。"""
    target = Path(path)
    if not target.is_absolute():
        raise ValueError("queue owner path 必须是绝对路径")
    read_failed = False
    try:
        raw = read_regular_file_bounded(target, max_bytes=_MAX_BYTES)
    except FileNotFoundError:
        return None
    except (MemoryError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        read_failed = True
    if read_failed:
        raise QueueOwnerUnavailableError("queue owner 文件无法安全读取")
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs_object,
            parse_constant=_reject_constant,
        )
        if type(payload) is not dict:
            raise ValueError("queue owner 根必须是对象")
        expected = {
            "schema_version",
            "owner_token",
            "backend_kind",
            "backend_fingerprint",
            "created_at",
        }
        if (
            set(payload) != expected
            or type(payload["schema_version"]) is not int
            or payload["schema_version"] != _SCHEMA_VERSION
        ):
            raise ValueError("queue owner schema 不匹配")
        return QueueOwnerIdentity(
            payload["owner_token"],
            QueueBackendBinding(
                payload["backend_kind"],
                payload["backend_fingerprint"],
            ),
            payload["created_at"],
        )
    except (TypeError, UnicodeError, ValueError, OverflowError, json.JSONDecodeError):
        pass
    raise QueueOwnerCorruptionError("queue owner 内容无效")


class QueueOwnerStore:
    """以独立锁和耐久替换维护稳定 queue owner。"""

    def __init__(
        self,
        *,
        path: Path | None = None,
        lock_timeout_sec: float = _LOCK_TIMEOUT_SEC,
    ) -> None:
        self._path = queue_owner_path() if path is None else Path(path)
        if not self._path.is_absolute():
            raise ValueError("queue owner path 必须是绝对路径")
        self._lock_path = self._path.with_name(f"{self._path.name}.lock")
        _Deadline.start(lock_timeout_sec)
        self._lock_timeout_sec = float(lock_timeout_sec)

    def load(self, binding: QueueBackendBinding) -> QueueOwnerIdentity:
        """正常启动只读取同一后端的既有 owner，绝不隐式创建或轮换。"""
        if type(binding) is not QueueBackendBinding:
            raise ValueError("binding 必须是 QueueBackendBinding")
        deadline = _Deadline.start(self._lock_timeout_sec)
        with advisory_lock(self._lock_path, deadline, blocking=True) as acquired:
            if not acquired:
                raise QueueOwnerTimeout("未取得 queue owner 锁")
            try:
                current = self._read_unlocked()
            except QueueOwnerCorruptionError:
                raise QueueOwnerRecoveryRequired(
                    "queue owner 文件损坏，必须先完成受控恢复"
                ) from None
            if current is None:
                raise QueueOwnerBootstrapRequired(
                    "queue owner 尚未初始化，禁止 worker 自动创建身份"
                )
            if current.binding != binding:
                raise QueueOwnerRecoveryRequired(
                    "queue owner 后端绑定不匹配，禁止 worker 自动轮换身份"
                )
            return current

    def initialize(
        self,
        binding: QueueBackendBinding,
        *,
        recovery_state_present: Callable[[float], bool],
    ) -> QueueOwnerIdentity:
        """仅供显式运维初始化缺失 owner，绝不覆盖损坏或绑定不匹配记录。"""
        if type(binding) is not QueueBackendBinding:
            raise ValueError("binding 必须是 QueueBackendBinding")
        if not callable(recovery_state_present):
            raise ValueError("recovery_state_present 必须可调用")
        deadline = _Deadline.start(self._lock_timeout_sec)
        with advisory_lock(self._lock_path, deadline, blocking=True) as acquired:
            if not acquired:
                raise QueueOwnerTimeout("未取得 queue owner 锁")
            try:
                current = self._read_unlocked()
            except QueueOwnerCorruptionError:
                raise QueueOwnerRecoveryRequired(
                    "queue owner 文件损坏，禁止覆盖且必须先完成受控恢复"
                ) from None
            if current is not None:
                if current.binding == binding:
                    return current
                raise QueueOwnerRecoveryRequired(
                    "queue owner 后端绑定不匹配，禁止覆盖且必须先完成受控迁移"
                )
            self._require_safe_initialization(recovery_state_present, deadline)
            created = QueueOwnerIdentity(uuid4().hex, binding, time.time())
            self._write_unlocked(created)
            return created

    @staticmethod
    def _require_safe_initialization(
        recovery_state_present: Callable[[float], bool],
        deadline: _Deadline,
    ) -> None:
        deadline.check()
        probe_failed = False
        try:
            present = recovery_state_present(deadline.remaining())
        except Exception:  # noqa: BLE001 - 无法证明安全时必须停止
            probe_failed = True
        if probe_failed:
            raise QueueOwnerRecoveryRequired("无法核对待恢复状态，拒绝初始化 queue owner")
        deadline.check()
        if type(present) is not bool:
            raise QueueOwnerRecoveryRequired("待恢复状态探针返回非法结果，拒绝初始化 queue owner")
        if present:
            raise QueueOwnerRecoveryRequired(
                "存在待恢复状态，拒绝初始化新的 queue owner"
            )

    def _read_unlocked(self) -> QueueOwnerIdentity | None:
        try:
            return read_queue_owner_file(self._path)
        except QueueOwnerUnavailableError:
            raise QueueOwnerCorruptionError("queue owner 文件无法安全读取") from None

    def _write_unlocked(self, identity: QueueOwnerIdentity) -> None:
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "owner_token": identity.token,
            "backend_kind": identity.binding.kind,
            "backend_fingerprint": identity.binding.fingerprint,
            "created_at": identity.created_at,
        }
        encode_failed = False
        try:
            raw = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, UnicodeError, ValueError):
            encode_failed = True
        if encode_failed:
            raise QueueOwnerCorruptionError("queue owner 无法规范编码")
        if len(raw) > _MAX_BYTES:
            raise QueueOwnerCorruptionError("queue owner 超过大小上限")
        durable_write_replace(self._path, raw)


__all__ = [
    "QueueBackendBinding",
    "QueueOwnerCorruptionError",
    "QueueOwnerError",
    "QueueOwnerIdentity",
    "QueueOwnerUnavailableError",
    "QueueOwnerBootstrapRequired",
    "QueueOwnerRecoveryRequired",
    "QueueOwnerStore",
    "QueueOwnerTimeout",
    "fingerprint_backend",
    "queue_owner_path",
    "read_queue_owner_file",
]
