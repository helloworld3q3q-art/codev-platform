"""在执行目标 Python 前证明基座 purelib 的逐文件来源。"""

from __future__ import annotations

import base64
import binascii
import csv
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat

from codev_platform.runtime_dependency_contract import DistributionPin


_MAX_FILES = 500_000
_MAX_TOTAL_BYTES = 64 * 1024 * 1024 * 1024
_MAX_METADATA_BYTES = 4 * 1024 * 1024
_MAX_RECORD_BYTES = 64 * 1024 * 1024
_MAX_RECORD_ROWS = _MAX_FILES
_MAX_PTH_BYTES = 64 * 1024
_MAX_PTH_TOTAL_BYTES = 1024 * 1024
_FORBIDDEN_STARTUP_FILES = frozenset({"sitecustomize.py", "usercustomize.py"})
_APPROVED_EXECUTABLE_PTH = {
    (
        "_cuda_bindings_redirector.pth",
        ("cuda-bindings",),
    ): "28d86507e791da835cf8b9dd21fe98b579b77b3ca27e3ad27bae6239bc94f769",
    (
        "distutils-precedence.pth",
        ("setuptools",),
    ): "2638ce9e2500e572a5e0de7faed6661eb569d1b696fcba07b0dd223da5f5d224",
}


class RuntimeDistributionInventoryError(RuntimeError):
    """基座发行包清单不能形成可信静态证明。"""


@dataclass(frozen=True, slots=True)
class DistributionInventoryProof:
    """purelib 完整来源清单的规范摘要。"""

    inventory_sha256: str
    distributions: tuple[str, ...]
    file_count: int
    total_bytes: int


@dataclass(frozen=True, slots=True)
class _RecordClaim:
    owners: tuple[str, ...]
    digest: str | None
    size: int | None


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    relative: str
    sha256: str
    size: int
    mode: int
    owners: tuple[str, ...]
    content: bytes | None


def verify_distribution_inventory(
    purelib: Path,
    pins: tuple[DistributionPin, ...],
) -> DistributionInventoryProof:
    """验证每个 purelib 文件恰好由锁内一个发行包的 RECORD 证明。"""
    root = _plain_directory(Path(purelib), "基座 purelib")
    expected = _expected_pins(pins)
    actual, directories = _walk_inventory(root)
    return _verify_inventory_snapshot(root, expected, actual, directories)


def _verify_inventory_snapshot(
    root: Path,
    expected: dict[str, str],
    actual: dict[str, Path],
    directories: set[str],
    *,
    claims: dict[str, _RecordClaim] | None = None,
    distributions: tuple[str, ...] | None = None,
) -> DistributionInventoryProof:
    """复用同一清单语义验证已取得的安全文件快照。"""
    _reject_startup_injection(actual)
    if claims is None or distributions is None:
        claims, distributions = _collect_claims(root, expected)
    if set(actual) != set(claims):
        raise RuntimeDistributionInventoryError("基座 purelib 含未知或未安装文件")
    _require_known_directories(directories, set(actual))
    snapshots = _verify_claims_with_pth_budget(actual, claims)
    _verify_pth_files(root, snapshots)
    payload = {
        "distributions": distributions,
        "files": [
            {
                "mode": item.mode,
                "owners": list(item.owners),
                "path": item.relative,
                "sha256": item.sha256,
                "size": item.size,
            }
            for item in snapshots
        ],
        "schema_version": 1,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return DistributionInventoryProof(
        inventory_sha256=hashlib.sha256(encoded).hexdigest(),
        distributions=distributions,
        file_count=len(snapshots),
        total_bytes=sum(item.size for item in snapshots),
    )


def _expected_pins(pins: tuple[DistributionPin, ...]) -> dict[str, str]:
    if type(pins) is not tuple or not pins:
        raise RuntimeDistributionInventoryError("依赖锁 pin 集合无效")
    expected: dict[str, str] = {}
    for pin in pins:
        if type(pin) is not DistributionPin or not pin.name or not pin.version:
            raise RuntimeDistributionInventoryError("依赖锁 pin 类型无效")
        if _canonical_name(pin.name) != pin.name or pin.name in expected:
            raise RuntimeDistributionInventoryError("依赖锁 pin 名称无效或重复")
        expected[pin.name] = pin.version
    return expected


def _walk_inventory(purelib: Path) -> tuple[dict[str, Path], set[str]]:
    files: dict[str, Path] = {}
    directories: set[str] = set()
    total_bytes = 0
    try:
        for current, names, filenames in os.walk(purelib, followlinks=False):
            current_path = Path(current)
            for name in names:
                path = current_path / name
                metadata = path.lstat()
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                    raise RuntimeDistributionInventoryError("基座 purelib 含链接或特殊目录")
                directories.add(path.relative_to(purelib).as_posix())
            for name in filenames:
                path = current_path / name
                metadata = path.lstat()
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                    raise RuntimeDistributionInventoryError("基座 purelib 含链接或特殊文件")
                relative = path.relative_to(purelib).as_posix()
                if relative in files:
                    raise RuntimeDistributionInventoryError("基座 purelib 文件路径重复")
                files[relative] = path
                total_bytes += metadata.st_size
                if len(files) > _MAX_FILES or total_bytes > _MAX_TOTAL_BYTES:
                    raise RuntimeDistributionInventoryError("基座 purelib 清单超过安全预算")
    except RuntimeDistributionInventoryError:
        raise
    except OSError:
        raise RuntimeDistributionInventoryError("基座 purelib 无法完整遍历") from None
    return files, directories


def _reject_startup_injection(actual: dict[str, Path]) -> None:
    for relative in actual:
        name = PurePosixPath(relative).name.lower()
        if relative.lower() in _FORBIDDEN_STARTUP_FILES or name.endswith(".egg-link"):
            raise RuntimeDistributionInventoryError("基座 purelib 含禁止的启动注入文件")


def _collect_claims(
    purelib: Path,
    expected: dict[str, str],
) -> tuple[dict[str, _RecordClaim], tuple[str, ...]]:
    claims: dict[str, _RecordClaim] = {}
    observed: dict[str, str] = {}
    try:
        candidates = sorted(path for path in purelib.iterdir() if path.name.endswith(".dist-info"))
    except OSError:
        raise RuntimeDistributionInventoryError("基座发行包元数据无法枚举") from None
    for candidate in candidates:
        dist_info = _plain_directory(candidate, "基座发行包元数据目录")
        name, version = _distribution_identity(dist_info)
        if name in observed:
            raise RuntimeDistributionInventoryError("基座发行包身份重复")
        observed[name] = version
        for relative, claim in _read_record(dist_info, purelib, name).items():
            existing = claims.get(relative)
            claims[relative] = claim if existing is None else _merge_claims(existing, claim)
    if observed != expected:
        raise RuntimeDistributionInventoryError("基座发行包与依赖锁精确 pin 不一致")
    distributions = tuple(f"{name}=={observed[name]}" for name in sorted(observed))
    return claims, distributions


def _merge_claims(first: _RecordClaim, second: _RecordClaim) -> _RecordClaim:
    """只合并内容和大小都已证明相同的共享命名空间文件声明。"""
    if (
        first.digest is None
        or first.size is None
        or first.digest != second.digest
        or first.size != second.size
        or set(first.owners) & set(second.owners)
    ):
        raise RuntimeDistributionInventoryError("多个发行包共享文件声明冲突")
    return _RecordClaim(
        owners=tuple(sorted((*first.owners, *second.owners))),
        digest=first.digest,
        size=first.size,
    )


def _distribution_identity(dist_info: Path) -> tuple[str, str]:
    content = _read_bounded_file(dist_info / "METADATA", _MAX_METADATA_BYTES, "METADATA")
    try:
        message = BytesParser(policy=policy.compat32).parsebytes(content)
    except (UnicodeError, ValueError):
        raise RuntimeDistributionInventoryError("基座发行包 METADATA 无效") from None
    names = message.get_all("Name", [])
    versions = message.get_all("Version", [])
    if (
        len(names) != 1
        or len(versions) != 1
        or not isinstance(names[0], str)
        or not isinstance(versions[0], str)
    ):
        raise RuntimeDistributionInventoryError("基座发行包身份无效")
    name = _canonical_name(names[0])
    version = versions[0]
    if not name or not version or any(char.isspace() for char in version):
        raise RuntimeDistributionInventoryError("基座发行包身份无效")
    directory_prefix = name.replace("-", "_") + "_"
    directory_stem = dist_info.name.removesuffix(".dist-info").lower().replace("-", "_")
    if not directory_stem.startswith(directory_prefix):
        raise RuntimeDistributionInventoryError("基座发行包目录与身份不一致")
    return name, version


def _read_record(
    dist_info: Path,
    purelib: Path,
    owner: str,
) -> dict[str, _RecordClaim]:
    record = dist_info / "RECORD"
    content = _read_bounded_file(record, _MAX_RECORD_BYTES, "RECORD")
    try:
        rows = csv.reader(io.StringIO(content.decode("utf-8"), newline=""))
        claims = _record_rows(rows, purelib, owner)
    except (csv.Error, UnicodeError):
        raise RuntimeDistributionInventoryError("基座发行包 RECORD 无效") from None
    record_relative = record.relative_to(purelib).as_posix()
    own_claim = claims.get(record_relative)
    if own_claim is None or own_claim.digest is not None or own_claim.size is not None:
        raise RuntimeDistributionInventoryError("基座发行包 RECORD 自身条目无效")
    _require_proven_unhashed_entries(claims, record_relative)
    return claims


def _record_rows(
    rows: csv.reader,
    purelib: Path,
    owner: str,
) -> dict[str, _RecordClaim]:
    claims: dict[str, _RecordClaim] = {}
    for row_number, row in enumerate(rows, start=1):
        if row_number > _MAX_RECORD_ROWS:
            raise RuntimeDistributionInventoryError("基座发行包 RECORD 行数超过安全预算")
        if len(row) != 3:
            raise RuntimeDistributionInventoryError("基座发行包 RECORD 行格式无效")
        relative = _record_target_in_purelib(purelib, row[0])
        if relative is None:
            continue
        if relative in claims:
            raise RuntimeDistributionInventoryError("基座发行包 RECORD 路径重复")
        claims[relative] = _RecordClaim(
            owners=(owner,),
            digest=_record_digest(row[1]),
            size=_record_size(row[2]),
        )
    return claims


def _record_target_in_purelib(purelib: Path, value: str) -> str | None:
    if not value or "\\" in value or "\x00" in value or value.startswith("/"):
        raise RuntimeDistributionInventoryError("基座发行包 RECORD 路径无效")
    normalized = posixpath.normpath(value)
    path = PurePosixPath(normalized)
    if normalized != value or any(":" in part for part in path.parts):
        raise RuntimeDistributionInventoryError("基座发行包 RECORD 路径未规范化")
    if not path.parts or ".." in path.parts:
        return None
    return purelib.joinpath(*path.parts).relative_to(purelib).as_posix()


def _record_digest(value: str) -> str | None:
    if not value:
        return None
    algorithm, separator, encoded = value.partition("=")
    if algorithm != "sha256" or separator != "=" or not encoded:
        raise RuntimeDistributionInventoryError("基座发行包 RECORD 必须使用 sha256")
    try:
        digest = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error):
        raise RuntimeDistributionInventoryError("基座发行包 RECORD 哈希编码无效") from None
    if len(digest) != hashlib.sha256().digest_size:
        raise RuntimeDistributionInventoryError("基座发行包 RECORD 哈希长度无效")
    return digest.hex()


def _record_size(value: str) -> int | None:
    if not value:
        return None
    if len(value) > 20 or not value.isascii() or not value.isdecimal():
        raise RuntimeDistributionInventoryError("基座发行包 RECORD 大小无效")
    size = int(value)
    if size > _MAX_TOTAL_BYTES:
        raise RuntimeDistributionInventoryError("基座发行包 RECORD 文件超过安全预算")
    return size


def _require_proven_unhashed_entries(
    claims: dict[str, _RecordClaim],
    record_relative: str,
) -> None:
    for relative, claim in claims.items():
        if claim.digest is not None or relative == record_relative:
            continue
        source = _pyc_source(relative)
        source_claim = claims.get(source) if source is not None else None
        if source_claim is None or source_claim.digest is None:
            raise RuntimeDistributionInventoryError("基座发行包 RECORD 含无法证明的空哈希文件")


def _pyc_source(relative: str) -> str | None:
    path = PurePosixPath(relative)
    if path.suffix != ".pyc":
        return None
    if path.parent.name == "__pycache__":
        return (path.parent.parent / f"{path.name.partition('.')[0]}.py").as_posix()
    return path.with_suffix(".py").as_posix()


def _verify_claim(path: Path, relative: str, claim: _RecordClaim) -> _FileSnapshot:
    is_pth = relative.lower().endswith(".pth")
    content, digest, metadata = _snapshot_file(
        path,
        max_bytes=_MAX_PTH_BYTES if is_pth else _MAX_TOTAL_BYTES,
        collect=is_pth,
    )
    if claim.size is not None and claim.size != metadata.st_size:
        raise RuntimeDistributionInventoryError("基座发行包 RECORD 文件大小不一致")
    if claim.digest is not None and claim.digest != digest:
        raise RuntimeDistributionInventoryError("基座发行包 RECORD 文件摘要不一致")
    return _FileSnapshot(
        relative,
        digest,
        metadata.st_size,
        stat.S_IMODE(metadata.st_mode),
        claim.owners,
        content,
    )


def _verify_claims_with_pth_budget(
    actual: dict[str, Path],
    claims: dict[str, _RecordClaim],
) -> tuple[_FileSnapshot, ...]:
    """顺序验证文件声明，并限制待检查 .pth 快照的聚合内存。"""
    snapshots: list[_FileSnapshot] = []
    pth_total_bytes = 0
    for relative in sorted(actual):
        snapshot = _verify_claim(actual[relative], relative, claims[relative])
        if snapshot.content is not None:
            pth_total_bytes += len(snapshot.content)
            if pth_total_bytes > _MAX_PTH_TOTAL_BYTES:
                raise RuntimeDistributionInventoryError("基座 .pth 聚合内容超过安全预算")
        snapshots.append(snapshot)
    return tuple(snapshots)


def _snapshot_file(
    path: Path,
    *,
    max_bytes: int,
    collect: bool,
) -> tuple[bytes | None, str, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise RuntimeDistributionInventoryError("基座 purelib 文件不可安全打开") from None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
            raise RuntimeDistributionInventoryError("基座 purelib 文件类型或大小无效")
        blocks: list[bytes] | None = [] if collect else None
        digest = hashlib.sha256()
        total = 0
        while block := os.read(descriptor, min(1024 * 1024, before.st_size + 1 - total)):
            if blocks is not None:
                blocks.append(block)
            digest.update(block)
            total += len(block)
            if total > before.st_size:
                raise RuntimeDistributionInventoryError("基座 purelib 文件读取大小漂移")
        after = os.fstat(descriptor)
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or total != after.st_size
        ):
            raise RuntimeDistributionInventoryError("基座 purelib 文件读取期间发生漂移")
        return b"".join(blocks) if blocks is not None else None, digest.hexdigest(), after
    except RuntimeDistributionInventoryError:
        raise
    except OSError:
        raise RuntimeDistributionInventoryError("基座 purelib 文件不可安全读取") from None
    finally:
        os.close(descriptor)


def _read_bounded_file(path: Path, limit: int, label: str) -> bytes:
    content, _digest, metadata = _snapshot_file(path, max_bytes=limit, collect=True)
    if content is None or metadata.st_size > limit:
        raise RuntimeDistributionInventoryError(f"基座发行包 {label} 超过安全预算")
    return content


def _verify_pth_files(purelib: Path, snapshots: tuple[_FileSnapshot, ...]) -> None:
    for item in snapshots:
        if not item.relative.lower().endswith(".pth"):
            continue
        path = purelib / item.relative
        if item.size > _MAX_PTH_BYTES or item.content is None:
            raise RuntimeDistributionInventoryError("基座 .pth 文件超过安全预算")
        try:
            lines = item.content.decode("utf-8").splitlines()
        except UnicodeError:
            raise RuntimeDistributionInventoryError("基座 .pth 文件不可读") from None
        executable = False
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith(("import ", "import\t")):
                executable = True
                continue
            target = _pth_target_in_purelib(purelib, line)
            if target.is_symlink() or not target.is_dir():
                raise RuntimeDistributionInventoryError("基座 .pth 目标目录无效")
        if executable and _APPROVED_EXECUTABLE_PTH.get((item.relative, item.owners)) != item.sha256:
            raise RuntimeDistributionInventoryError("基座 .pth 含未批准的可执行启动代码")
        try:
            _content, digest, metadata = _snapshot_file(
                path,
                max_bytes=_MAX_PTH_BYTES,
                collect=False,
            )
        except RuntimeDistributionInventoryError:
            raise RuntimeDistributionInventoryError("基座 .pth 检查期间发生替换") from None
        if (
            digest != item.sha256
            or metadata.st_size != item.size
            or stat.S_IMODE(metadata.st_mode) != item.mode
        ):
            raise RuntimeDistributionInventoryError("基座 .pth 检查期间发生替换")


def _pth_target_in_purelib(purelib: Path, value: str) -> Path:
    candidate = Path(os.path.normpath(value))
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise RuntimeDistributionInventoryError("基座 .pth 路径逃逸 purelib")
    target = purelib.joinpath(*candidate.parts)
    try:
        target.relative_to(purelib)
    except ValueError:
        raise RuntimeDistributionInventoryError("基座 .pth 路径逃逸 purelib") from None
    return target


def _require_known_directories(directories: set[str], files: set[str]) -> None:
    allowed = {
        parent.as_posix()
        for relative in files
        for parent in PurePosixPath(relative).parents
        if parent.as_posix() != "."
    }
    if not directories.issubset(allowed):
        raise RuntimeDistributionInventoryError("基座 purelib 包含未知空目录")


def _plain_directory(path: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError:
        raise RuntimeDistributionInventoryError(f"{label}不可用") from None
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
        raise RuntimeDistributionInventoryError(f"{label}必须是非链接目录")
    return path


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


__all__ = [
    "DistributionInventoryProof",
    "RuntimeDistributionInventoryError",
    "verify_distribution_inventory",
]
