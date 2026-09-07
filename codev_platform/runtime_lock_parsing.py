"""运行时依赖锁的纯解析、来源门禁与身份重算。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from urllib.parse import urlsplit

from codev_platform.core.runtime_models import RequirementsLockInfo
from codev_platform.runtime_dependency_contract import (
    DistributionPin,
    LockedWheelArtifact,
    REQUIRED_RUNTIME_DISTRIBUTIONS,
    RequirementsLockContract,
)


LOCK_HEADER = "# codev-platform-runtime-lock-v1"
ARTIFACT_PREFIX = "# codev-artifact "
INDEX_OPTION = "--extra-index-url"
PRIMARY_INDEX_OPTION = "--index-url"
_OFFICIAL_INDEX_HOST = "download.pytorch.org"
_DEFAULT_PRIMARY_INDEX_URL = "https://pypi.org/simple"
_APPROVED_PRIMARY_INDEX_URLS = frozenset(
    {
        _DEFAULT_PRIMARY_INDEX_URL,
        "https://pypi.tuna.tsinghua.edu.cn/simple",
    }
)
_CUDA_PATH_RE = re.compile(r"/whl/(?P<tag>cu[0-9]+)/?\Z")
_CUDA_VERSION_RE = re.compile(r"\+(?P<tag>cu[0-9]+)(?:\.|\Z)")
_NAME_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?\Z")
_PIN_RE = re.compile(
    r"(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"==(?P<version>[A-Za-z0-9](?:[A-Za-z0-9._+!-]*[A-Za-z0-9])?)\Z"
)
_LOCK_PIN_RE = re.compile(
    r"(?P<pin>"
    r"(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"==(?P<version>[A-Za-z0-9](?:[A-Za-z0-9._+!-]*[A-Za-z0-9])?)"
    r") (?P<hashes>--hash=sha256:[0-9a-f]{64}(?: --hash=sha256:[0-9a-f]{64})*)\Z"
)
_HASH_TOKEN_RE = re.compile(r"--hash=sha256:(?P<digest>[0-9a-f]{64})\Z")
_PROJECT_EDITABLE_RE = re.compile(
    r"(?:[#&])egg=codev[-_]platform(?:[&#]|\Z)",
    re.IGNORECASE,
)
_MAX_INPUT_BYTES = 4 * 1024 * 1024


class RuntimeLockError(RuntimeError):
    """依赖来源、pin 或制品摘要不能形成可信锁。"""


Pin = DistributionPin
Artifact = LockedWheelArtifact


def fail(message: str) -> RuntimeLockError:
    return RuntimeLockError(message)


def canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def read_text(path: Path, *, kind: str) -> str:
    return _decode_snapshot(_read_file_snapshot(path, kind=kind), kind=kind)


def _read_file_snapshot(path: Path, *, kind: str) -> bytes:
    """从单个不跟随链接的文件描述符读取有界稳定快照。"""
    target = Path(path)
    descriptor: int | None = None
    try:
        linked = target.lstat()
        if (
            not stat.S_ISREG(linked.st_mode)
            or stat.S_ISLNK(linked.st_mode)
            or linked.st_nlink != 1
            or linked.st_size > _MAX_INPUT_BYTES
        ):
            raise OSError
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_BINARY", 0)
        )
        descriptor = os.open(target, flags)
        opened = os.fstat(descriptor)
        if _file_identity(linked) != _file_identity(opened):
            raise OSError
        blocks: list[bytes] = []
        total = 0
        while True:
            block = os.read(descriptor, min(8192, _MAX_INPUT_BYTES + 1 - total))
            if not block:
                break
            blocks.append(block)
            total += len(block)
            if total > _MAX_INPUT_BYTES:
                raise OSError
        after = os.fstat(descriptor)
        if _file_identity(opened) != _file_identity(after) or total != after.st_size:
            raise OSError
        return b"".join(blocks)
    except OSError:
        raise fail(f"{kind}文件不可用") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _decode_snapshot(content: bytes, *, kind: str) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeError:
        raise fail(f"{kind}文件不可用") from None


def _file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    identity = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
    )
    return (*identity, metadata.st_ctime_ns) if os.name == "posix" else identity


def require_index_url(value: object) -> tuple[str, str]:
    if type(value) is not str or not value:
        raise fail("批准索引地址无效")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise fail("批准索引地址无效") from None
    match = _CUDA_PATH_RE.fullmatch(parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.hostname != _OFFICIAL_INDEX_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or match is None
        or any(char.isspace() or ord(char) < 32 for char in value)
    ):
        raise fail("批准索引必须是无凭据的官方 HTTPS CUDA 索引")
    return value, match.group("tag")


def approved_index_url(approved_source: Path) -> tuple[str, str]:
    """兼容接口：返回 CUDA extra index 及其 tag。"""
    _primary, cuda, tag = approved_indexes(approved_source)
    return cuda, tag


def approved_indexes(approved_source: Path) -> tuple[str, str, str]:
    """从批准文件读取唯一主镜像和唯一官方 CUDA extra index。"""
    text = read_text(approved_source, kind="批准索引")
    candidates: list[str] = []
    primary_candidates: list[str] = []
    forbidden_option = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(f"{INDEX_OPTION}="):
            candidates.append(line.removeprefix(f"{INDEX_OPTION}="))
            continue
        if line.startswith(f"{INDEX_OPTION} "):
            parts = line.split()
            if len(parts) == 2:
                candidates.append(parts[1])
                continue
        if line.startswith(f"{PRIMARY_INDEX_OPTION}="):
            primary_candidates.append(line.removeprefix(f"{PRIMARY_INDEX_OPTION}="))
            continue
        if line.startswith(f"{PRIMARY_INDEX_OPTION} "):
            parts = line.split()
            if len(parts) == 2:
                primary_candidates.append(parts[1])
                continue
        if line.startswith(INDEX_OPTION):
            forbidden_option = True
            continue
        if line.startswith(PRIMARY_INDEX_OPTION):
            forbidden_option = True
            continue
        if line.startswith(("--find-links", "--trusted-host")):
            forbidden_option = True
    if forbidden_option or len(candidates) != 1:
        raise fail("批准索引必须恰好包含一条官方 HTTPS CUDA 索引")
    if len(primary_candidates) > 1:
        raise fail("批准主镜像最多只能有一条")
    primary = primary_candidates[0] if primary_candidates else _DEFAULT_PRIMARY_INDEX_URL
    if primary not in _APPROVED_PRIMARY_INDEX_URLS:
        raise fail("批准主镜像不在固定 HTTPS 白名单")
    cuda, tag = require_index_url(candidates[0])
    return primary, cuda, tag


def parse_pin(line: str, *, kind: str) -> Pin:
    match = _PIN_RE.fullmatch(line)
    if match is None:
        raise fail(f"{kind}只接受无标记的精确 pin")
    name = canonical_name(match.group("name"))
    version = match.group("version")
    if name == "codev-platform":
        raise fail(f"{kind}不能把应用自身写入依赖基座")
    return Pin(name, version)


def parse_freeze(raw_freeze: Path) -> tuple[Pin, ...]:
    text = read_text(raw_freeze, kind="freeze")
    pins: dict[str, Pin] = {}
    project_editable_count = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if _is_project_editable(line):
            project_editable_count += 1
            continue
        if line.startswith(("-e ", "--editable ")):
            raise fail("freeze 含非项目可编辑依赖")
        pin = parse_pin(line, kind="freeze")
        if pin.name in pins:
            raise fail("freeze 含重复发行包")
        pins[pin.name] = pin
    if project_editable_count > 1:
        raise fail("freeze 项目可编辑记录不唯一")
    require_managed_distributions(pins)
    return tuple(pins[name] for name in sorted(pins))


def _is_project_editable(line: str) -> bool:
    return line.startswith(("-e ", "--editable ")) and _PROJECT_EDITABLE_RE.search(line) is not None


def require_managed_distributions(pins: dict[str, Pin]) -> None:
    if not REQUIRED_RUNTIME_DISTRIBUTIONS.issubset(pins):
        raise fail("freeze 缺少受管服务必需发行包")


def require_cuda_contract(pins: dict[str, Pin], index_tag: str) -> frozenset[str]:
    tags: set[str] = set()
    for pin in pins.values():
        match = _CUDA_VERSION_RE.search(pin.version)
        if match is not None:
            tags.add(match.group("tag"))
    torch = pins.get("torch")
    torch_match = _CUDA_VERSION_RE.search(torch.version) if torch is not None else None
    if torch_match is None or torch_match.group("tag") != index_tag or tags != {index_tag}:
        raise fail("Torch CUDA tag 与批准索引不一致")
    return frozenset(tags)


def parse_artifact(line: str) -> Artifact:
    parts = line.split()
    if len(parts) != 5 or parts[:2] != ["#", "codev-artifact"]:
        raise fail("锁内制品声明无效")
    package, filename, digest = parts[2:]
    if (
        _NAME_RE.fullmatch(package) is None
        or canonical_name(package) != package
        or not filename.endswith(".whl")
        or not filename.isascii()
        or Path(filename).name != filename
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        raise fail("锁内制品声明无效")
    return Artifact(package=package, filename=filename, sha256=digest)


def parse_lock_pin(line: str) -> tuple[Pin, frozenset[str]]:
    match = _LOCK_PIN_RE.fullmatch(line)
    if match is None:
        raise fail("锁内依赖必须是带 SHA-256 的精确 pin")
    pin = parse_pin(match.group("pin"), kind="锁")
    hashes = frozenset(
        token_match.group("digest")
        for token in match.group("hashes").split()
        if (token_match := _HASH_TOKEN_RE.fullmatch(token)) is not None
    )
    if not hashes:
        raise fail("锁内每个 pin 至少需要一个 SHA-256")
    return pin, hashes


def validate_artifact_filename(artifact: Artifact, pin: Pin) -> None:
    parts = artifact.filename.removesuffix(".whl").split("-")
    if len(parts) < 5 or canonical_name(parts[0]) != pin.name or parts[1] != pin.version:
        raise fail("锁内 wheel 与精确 pin 不一致")


def manifest_sha256(artifacts: tuple[Artifact, ...]) -> str:
    manifest = [
        {"filename": artifact.filename, "sha256": artifact.sha256}
        for artifact in sorted(artifacts, key=lambda item: item.filename)
    ]
    encoded = json.dumps(
        manifest,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def inspect_requirements_lock_contract(
    path: Path,
    expected_index_url: str,
    expected_primary_index_url: str | None = None,
) -> RequirementsLockContract:
    """只依赖保存的锁与显式预期索引，重算完整锁身份。"""
    approved_url, index_tag = require_index_url(expected_index_url)
    target = Path(path)
    encoded = _read_file_snapshot(target, kind="锁")
    text = _decode_snapshot(encoded, kind="锁")
    pins: dict[str, Pin] = {}
    hashes_by_package: dict[str, frozenset[str]] = {}
    artifacts: list[Artifact] = []
    artifact_names: set[str] = set()
    index_values: list[str] = []
    primary_index_values: list[str] = []
    header_count = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line == LOCK_HEADER:
            header_count += 1
            continue
        if line.startswith(ARTIFACT_PREFIX):
            artifact = parse_artifact(line)
            if artifact.filename in artifact_names:
                raise fail("锁内制品文件名重复")
            artifact_names.add(artifact.filename)
            artifacts.append(artifact)
            continue
        if line.startswith(f"{INDEX_OPTION} "):
            parts = line.split()
            if len(parts) != 2:
                raise fail("锁内批准索引声明无效")
            index_values.append(parts[1])
            continue
        if line.startswith(f"{PRIMARY_INDEX_OPTION} "):
            parts = line.split()
            if len(parts) != 2:
                raise fail("锁内批准主镜像声明无效")
            primary_index_values.append(parts[1])
            continue
        if line.startswith("#"):
            raise fail("锁内含未知元数据")
        pin, hashes = parse_lock_pin(line)
        if pin.name in pins:
            raise fail("锁内含重复发行包")
        pins[pin.name] = pin
        hashes_by_package[pin.name] = hashes
    if header_count != 1 or index_values != [approved_url]:
        raise fail("锁内批准索引或 schema 不一致")
    if len(primary_index_values) != 1:
        raise fail("锁内批准主镜像或 schema 不一致")
    primary = primary_index_values[0]
    if primary not in _APPROVED_PRIMARY_INDEX_URLS:
        raise fail("锁内批准主镜像不在固定 HTTPS 白名单")
    if expected_primary_index_url is not None and primary != expected_primary_index_url:
        raise fail("锁内批准主镜像与预期不一致")
    require_managed_distributions(pins)
    cuda_tags = require_cuda_contract(pins, index_tag)
    artifacts_by_package: dict[str, set[str]] = {}
    for artifact in artifacts:
        pin = pins.get(artifact.package)
        if pin is None:
            raise fail("锁内制品没有对应 pin")
        validate_artifact_filename(artifact, pin)
        artifacts_by_package.setdefault(artifact.package, set()).add(artifact.sha256)
    for name, hashes in hashes_by_package.items():
        if artifacts_by_package.get(name) != set(hashes):
            raise fail("锁内 pin 与制品 SHA-256 不一致")
    info = RequirementsLockInfo(
        requirements_sha256=hashlib.sha256(encoded).hexdigest(),
        approved_index_url=approved_url,
        pin_count=len(pins),
        artifact_manifest_sha256=manifest_sha256(tuple(artifacts)),
        cuda_tags=cuda_tags,
    )
    return RequirementsLockContract(
        info=info,
        pins=tuple(pins[name] for name in sorted(pins)),
        artifacts=tuple(sorted(artifacts, key=lambda item: item.filename)),
    )


def inspect_requirements_lock(
    path: Path,
    expected_index_url: str,
    expected_primary_index_url: str | None = None,
) -> RequirementsLockInfo:
    """兼容接口：返回一次完整锁解析得到的身份部分。"""
    return inspect_requirements_lock_contract(
        path,
        expected_index_url,
        expected_primary_index_url,
    ).info


__all__ = [
    "ARTIFACT_PREFIX",
    "Artifact",
    "INDEX_OPTION",
    "LOCK_HEADER",
    "PRIMARY_INDEX_OPTION",
    "Pin",
    "RuntimeLockError",
    "approved_index_url",
    "approved_indexes",
    "canonical_name",
    "fail",
    "inspect_requirements_lock",
    "inspect_requirements_lock_contract",
    "manifest_sha256",
    "parse_freeze",
    "parse_pin",
    "require_cuda_contract",
]
