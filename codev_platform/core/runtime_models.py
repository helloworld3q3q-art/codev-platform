"""版本化运行时使用的叶子模型、摘要与严格编解码。"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import platform
import re
import sys
import sysconfig
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

RuntimeMode = Literal["release", "editable", "installed"]
RUNTIME_ACCESS_PROFILE = "root-service-group-read-v1"
_OID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
_RUNTIME_IDENTITY_OPTIONAL_FIELDS = frozenset(
    {
        "release_id",
        "wheel_sha256",
        "base_id",
        "base_requirements_sha256",
        "source_root",
    }
)
_HASH_IDENTITY_FIELDS = frozenset({"base_id", "release_id", "active_release", "previous_release"})


class RuntimeModelError(ValueError):
    """运行时模型或元数据不符合固定契约。"""


def _text(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise RuntimeModelError(f"{field} 必须是非空字符串")
    return value


def _schema(value: object, expected: int) -> None:
    if type(value) is not int or value != expected:
        raise RuntimeModelError(f"schema_version 只接受整数 {expected}")


def _oid(value: object, field: str) -> None:
    if type(value) is not str or _OID_RE.fullmatch(value) is None or set(value) == {"0"}:
        raise RuntimeModelError(f"{field} 必须是完整小写 Git OID")


def require_runtime_revision(value: object, *, git_only: bool = False) -> str:
    """返回规范运行时版本；发布门禁可进一步只接受 40 位 Git OID。"""
    if type(git_only) is not bool:
        raise RuntimeModelError("git_only 必须是布尔值")
    _oid(value, "runtime_revision")
    if git_only and len(value) != 40:
        raise RuntimeModelError("runtime_revision 必须是 40 位 Git OID")
    return value


def _sha(value: object, field: str) -> None:
    if type(value) is not str or _SHA_RE.fullmatch(value) is None or set(value) == {"0"}:
        raise RuntimeModelError(f"{field} 必须是完整小写 SHA-256")


def require_sha256(value: object, *, field: str = "sha256") -> str:
    """返回规范 SHA-256，供跨模块身份契约复用同一验证真值。"""
    if type(field) is not str or not field:
        raise RuntimeModelError("摘要字段名必须是非空字符串")
    _sha(value, field)
    return value


def _relative(value: object, field: str) -> None:
    raw = _text(value, field)
    normalized = raw.replace("\\", "/")
    parts = normalized.split("/")
    windows = PureWindowsPath(raw)
    if (
        PurePosixPath(normalized).is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} for part in parts)
        or "\x00" in raw
    ):
        raise RuntimeModelError(f"{field} 必须是安全相对路径")


class _ValidatedModel:
    def __post_init__(self) -> None:
        for field in dataclasses.fields(self):
            name, value = field.name, getattr(self, field.name)
            if value is None and _is_optional_model_field(self, name):
                continue
            _validate_model_field(self, name, value)
        if type(self) is RuntimeIdentity:
            _validate_runtime_identity(self)


def _is_optional_model_field(model: object, name: str) -> bool:
    return (type(model) is RuntimeIdentity and name in _RUNTIME_IDENTITY_OPTIONAL_FIELDS) or (
        type(model) is ActivationResult and name == "previous_release"
    )


def _validate_model_field(model: object, name: str, value: object) -> None:
    if name == "schema_version":
        expected = 3 if type(model) is BaseMetadata else 1
        _schema(value, expected)
        return
    if name == "access_profile":
        if type(value) is not str or value != RUNTIME_ACCESS_PROFILE:
            raise RuntimeModelError(f"access_profile 只接受 {RUNTIME_ACCESS_PROFILE}")
        return
    if name == "runtime_revision":
        require_runtime_revision(value)
        return
    if name.endswith("_sha256") or name in _HASH_IDENTITY_FIELDS:
        require_sha256(value, field=name)
        return
    if name.endswith("_relative") or name == "wheel_name":
        _relative(value, name)
        return
    if name == "mode":
        _validate_runtime_mode(value)
        return
    if name == "abi":
        _validate_runtime_abi(value)
        return
    if name == "pin_count":
        _validate_pin_count(value)
        return
    if name == "cuda_tags":
        _validate_cuda_tags(value)
        return
    if name == "release_root":
        _validate_release_root(value)
        return
    _text(value, name)


def _validate_runtime_mode(value: object) -> None:
    if value not in ("release", "editable", "installed"):
        raise RuntimeModelError("mode 不受支持")


def _validate_runtime_abi(value: object) -> None:
    if type(value) is not RuntimeAbi:
        raise RuntimeModelError("abi 必须是 RuntimeAbi")


def _validate_pin_count(value: object) -> None:
    if type(value) is not int or value < 0:
        raise RuntimeModelError("pin_count 必须是非负整数")


def _validate_cuda_tags(value: object) -> None:
    if type(value) is not frozenset:
        raise RuntimeModelError("cuda_tags 必须是 frozenset")
    for tag in value:
        _text(tag, "cuda_tags")


def _validate_release_root(value: object) -> None:
    if not isinstance(value, Path):
        raise RuntimeModelError("release_root 必须是 Path")


@dataclass(frozen=True)
class RuntimeAbi(_ValidatedModel):
    implementation: str
    python_version: str
    cache_tag: str
    soabi: str
    platform_tag: str
    machine: str


@dataclass(frozen=True)
class RequirementsLockInfo(_ValidatedModel):
    requirements_sha256: str
    approved_index_url: str
    pin_count: int
    artifact_manifest_sha256: str
    cuda_tags: frozenset[str]


@dataclass(frozen=True)
class BaseMetadata(_ValidatedModel):
    schema_version: int
    access_profile: str
    base_id: str
    requirements_sha256: str
    approved_index_url: str
    artifact_manifest_sha256: str
    freeze_sha256: str
    purelib_inventory_sha256: str
    abi: RuntimeAbi
    created_at: str
    python_relative: str
    purelib_relative: str
    bin_relative: str
    lock_relative: str


@dataclass(frozen=True)
class ReleaseCandidate(_ValidatedModel):
    schema_version: int
    runtime_revision: str
    wheel_name: str
    wheel_sha256: str


@dataclass(frozen=True)
class ReleaseMetadata(_ValidatedModel):
    schema_version: int
    release_id: str
    runtime_revision: str
    wheel_sha256: str
    base_id: str
    base_requirements_sha256: str
    base_metadata_sha256: str
    app_freeze_sha256: str
    created_at: str
    python_relative: str
    purelib_relative: str
    base_link_relative: str
    base_pth_relative: str


@dataclass(frozen=True)
class RuntimeIdentity(_ValidatedModel):
    mode: RuntimeMode
    runtime_revision: str
    release_id: str | None
    wheel_sha256: str | None
    base_id: str | None
    base_requirements_sha256: str | None
    interpreter_realpath: str
    environment_prefix: str
    source_root: str | None

    def as_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


def _validate_runtime_identity(identity: RuntimeIdentity) -> None:
    release_fields = (
        identity.release_id,
        identity.wheel_sha256,
        identity.base_id,
        identity.base_requirements_sha256,
    )
    if identity.mode == "release":
        consistent = (
            all(value is not None for value in release_fields) and identity.source_root is None
        )
    elif identity.mode == "editable":
        consistent = (
            all(value is None for value in release_fields) and identity.source_root is not None
        )
    else:
        consistent = all(value is None for value in release_fields) and identity.source_root is None
    if not consistent:
        raise RuntimeModelError("mode 与运行时身份字段不一致")


@dataclass(frozen=True)
class ActivationResult(_ValidatedModel):
    active_release: str
    previous_release: str | None


@dataclass(frozen=True)
class SystemdRuntime(_ValidatedModel):
    release_root: Path

    @property
    def python(self) -> Path:
        return self.release_root / "current" / "venv" / "bin" / "python"


def current_abi() -> RuntimeAbi:
    """读取当前解释器的稳定 ABI 身份。"""
    cache_tag = sys.implementation.cache_tag
    soabi = sysconfig.get_config_var("SOABI")
    if not cache_tag or not soabi:
        raise RuntimeModelError("当前解释器缺少 cache_tag 或 SOABI")
    return RuntimeAbi(
        implementation=sys.implementation.name,
        python_version=platform.python_version(),
        cache_tag=cache_tag,
        soabi=str(soabi),
        platform_tag=sysconfig.get_platform(),
        machine=platform.machine(),
    )


def _json_value(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _json_value(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_json_value(item) for item in sorted(value)]
    if isinstance(value, Path):
        return str(value)
    return value


def canonical_json_bytes(value: object) -> bytes:
    """生成排序、紧凑且禁止非有限数的规范 JSON 字节。"""
    try:
        return json.dumps(
            _json_value(value),
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RuntimeModelError("无法生成规范 JSON") from exc


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def compute_base_id(requirements_sha256: str, abi: RuntimeAbi) -> str:
    _sha(requirements_sha256, "requirements_sha256")
    if type(abi) is not RuntimeAbi:
        raise RuntimeModelError("abi 必须是 RuntimeAbi")
    payload = {
        "abi": abi,
        "access_profile": RUNTIME_ACCESS_PROFILE,
        "requirements_sha256": requirements_sha256,
        "schema_version": 3,
    }
    return canonical_sha256(payload)


def compute_release_id(
    runtime_revision: str, wheel_sha256: str, base_id: str, base_metadata_sha256: str
) -> str:
    require_runtime_revision(runtime_revision)
    hashes = {
        "wheel_sha256": wheel_sha256,
        "base_id": base_id,
        "base_metadata_sha256": base_metadata_sha256,
    }
    for field, value in hashes.items():
        require_sha256(value, field=field)
    return canonical_sha256({**hashes, "runtime_revision": runtime_revision, "schema_version": 1})


def read_base_metadata(path: Path) -> BaseMetadata:
    from .runtime_metadata_io import read_typed

    return read_typed(path, BaseMetadata)


def write_base_metadata_atomic(path: Path, value: BaseMetadata) -> None:
    from .runtime_metadata_io import write_typed_atomic

    write_typed_atomic(path, value, BaseMetadata)


def read_release_candidate(path: Path) -> ReleaseCandidate:
    from .runtime_metadata_io import read_typed

    return read_typed(path, ReleaseCandidate)


def write_release_candidate_atomic(path: Path, value: ReleaseCandidate) -> None:
    from .runtime_metadata_io import write_typed_atomic

    write_typed_atomic(path, value, ReleaseCandidate)


def read_release_metadata(path: Path) -> ReleaseMetadata:
    from .runtime_metadata_io import read_typed

    return read_typed(path, ReleaseMetadata)


def write_release_metadata_atomic(path: Path, value: ReleaseMetadata) -> None:
    from .runtime_metadata_io import write_typed_atomic

    write_typed_atomic(path, value, ReleaseMetadata)
