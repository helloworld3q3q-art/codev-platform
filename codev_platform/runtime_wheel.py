"""静态证明应用 wheel 与薄版本中的已安装源码完全一致。"""

from __future__ import annotations

import base64
import binascii
import csv
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat

from codev_platform.runtime_wheel_archive import inspect_application_wheel
from codev_platform.runtime_wheel_contract import (
    PayloadFile,
    RuntimeWheelError,
    WheelPayloadProof,
)


_MAX_MEMBER_BYTES = 32 * 1024 * 1024
_MAX_PURELIB_FILES = 100_000
_MAX_PURELIB_BYTES = 512 * 1024 * 1024
_MAX_INSTALLED_RECORD_ROWS = _MAX_PURELIB_FILES
_BOOTSTRAP_DISTRIBUTIONS = frozenset({"pip", "setuptools"})


@dataclass(frozen=True, slots=True)
class _InstalledRecordFile:
    relative: str
    algorithm: str | None
    digest: str | None
    size: int | None


@dataclass(frozen=True, slots=True)
class _InstalledApplicationInventory:
    """wheel 安装校验与缓存适配器共享的已证明 purelib 快照上下文。"""

    application: dict[str, PayloadFile]
    bootstrap: dict[str, _InstalledRecordFile]
    allowed_files: frozenset[str]
    source_files: frozenset[str]


def verify_installed_application(
    proof: WheelPayloadProof,
    purelib: Path,
    controlled_base_pth: Path,
) -> None:
    """遍历整个 purelib，按来源逐文件证明且拒绝未知载荷。"""
    inventory, actual, directories = _collect_installed_application_inventory(
        proof,
        purelib,
        controlled_base_pth,
    )
    _verify_installed_application_snapshot(actual, directories, inventory)


def _collect_installed_application_inventory(
    proof: WheelPayloadProof,
    purelib: Path,
    controlled_base_pth: Path,
) -> tuple[_InstalledApplicationInventory, dict[str, Path], set[str]]:
    """收集 wheel 静态校验的唯一来源集合，不接受未复验的 wheel 证明。"""
    if not isinstance(proof, WheelPayloadProof):
        raise RuntimeWheelError("应用 wheel 载荷证明类型无效")
    try:
        current_proof = inspect_application_wheel(proof.wheel_path)
    except RuntimeWheelError:
        raise RuntimeWheelError("应用 wheel 在预检后发生漂移") from None
    if current_proof != proof:
        raise RuntimeWheelError("应用 wheel 载荷证明或 wheel 发生漂移")
    purelib_root = _plain_directory(Path(purelib), "版本 purelib")
    base_pth = _verified_base_pth(purelib_root, Path(controlled_base_pth))
    actual, directories = _installed_inventory(purelib_root)
    application = {item.installed_relative: item for item in proof.application_files}
    missing = set(application).difference(actual)
    if missing:
        raise RuntimeWheelError("已安装应用源码缺失")
    metadata = _application_metadata_files(actual, proof.dist_info_relative)
    bootstrap = _bootstrap_inventory(purelib_root, proof.dist_info_relative)
    reserved = set(application) | metadata | {base_pth}
    if reserved.intersection(bootstrap):
        raise RuntimeWheelError("purelib 文件来源重叠")
    allowed = reserved | set(bootstrap)
    return (
        _InstalledApplicationInventory(
            application=application,
            bootstrap=bootstrap,
            allowed_files=frozenset(allowed),
            source_files=frozenset(application) | frozenset(bootstrap),
        ),
        actual,
        directories,
    )


def _verify_installed_application_snapshot(
    actual: dict[str, Path],
    directories: set[str],
    inventory: _InstalledApplicationInventory,
) -> None:
    """对既有或虚拟删除后的文件快照复用完全相同的 wheel 规则。"""
    if type(inventory) is not _InstalledApplicationInventory:
        raise RuntimeWheelError("应用 wheel 安装清单上下文无效")
    unknown = set(actual).difference(inventory.allowed_files)
    if unknown:
        raise RuntimeWheelError("版本 purelib 包含未知应用源码或文件")
    _require_known_directories(directories, set(inventory.allowed_files))
    _verify_application_files(actual, inventory.application)
    _verify_bootstrap_files(actual, inventory.bootstrap)


def _verified_base_pth(purelib: Path, candidate: Path) -> str:
    if candidate.parent != purelib or candidate.suffix != ".pth":
        raise RuntimeWheelError("受控基座 pth 路径无效")
    path = _regular_file(candidate, "受控基座 pth")
    try:
        if path.stat().st_size > 4096:
            raise RuntimeWheelError("受控基座 pth 过大")
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise RuntimeWheelError("受控基座 pth 不可读") from None
    if (
        len(lines) != 1
        or not lines[0]
        or lines[0] != lines[0].strip()
        or not Path(lines[0]).is_absolute()
        or lines[0].lstrip().lower().startswith(("import ", "import\t"))
    ):
        raise RuntimeWheelError("受控基座 pth 内容无效")
    return path.name


def _installed_inventory(purelib: Path) -> tuple[dict[str, Path], set[str]]:
    files: dict[str, Path] = {}
    directories: set[str] = set()
    total_size = 0
    try:
        for current, names, filenames in os.walk(purelib, followlinks=False):
            current_path = Path(current)
            for name in names:
                directory = _plain_directory(current_path / name, "版本 purelib 子目录")
                directories.add(directory.relative_to(purelib).as_posix())
            for name in filenames:
                path = _regular_file(current_path / name, "版本 purelib 文件")
                relative = path.relative_to(purelib).as_posix()
                files[relative] = path
                total_size += path.stat().st_size
                if len(files) > _MAX_PURELIB_FILES or total_size > _MAX_PURELIB_BYTES:
                    raise RuntimeWheelError("版本 purelib 清单超过安全上限")
    except RuntimeWheelError:
        raise
    except OSError:
        raise RuntimeWheelError("版本 purelib 无法完整遍历") from None
    return files, directories


def _application_metadata_files(actual: dict[str, Path], dist_info: str) -> set[str]:
    prefix = f"{dist_info}/"
    metadata = {name for name in actual if name.startswith(prefix)}
    required = {f"{prefix}{name}" for name in ("METADATA", "WHEEL", "RECORD")}
    if not required.issubset(metadata):
        raise RuntimeWheelError("已安装应用分发元数据不完整")
    return metadata


def _bootstrap_inventory(
    purelib: Path, application_dist_info: str
) -> dict[str, _InstalledRecordFile]:
    inventory: dict[str, _InstalledRecordFile] = {}
    identities: set[str] = set()
    try:
        candidates = sorted(
            path
            for path in purelib.iterdir()
            if path.name.endswith(".dist-info") and path.name != application_dist_info
        )
    except OSError:
        raise RuntimeWheelError("bootstrap 分发目录无法枚举") from None
    for directory in candidates:
        dist_info = _plain_directory(directory, "bootstrap 分发元数据目录")
        identity = _bootstrap_identity(dist_info)
        if identity not in _BOOTSTRAP_DISTRIBUTIONS or identity in identities:
            raise RuntimeWheelError("版本 purelib 含未受控 bootstrap 分发")
        identities.add(identity)
        for relative, entry in _installed_record(dist_info, purelib).items():
            if relative in inventory:
                raise RuntimeWheelError("bootstrap RECORD 安装目标重复")
            inventory[relative] = entry
    return inventory


def _bootstrap_identity(dist_info: Path) -> str:
    metadata = _regular_file(dist_info / "METADATA", "bootstrap METADATA")
    try:
        if metadata.stat().st_size > _MAX_MEMBER_BYTES:
            raise RuntimeWheelError("bootstrap METADATA 过大")
        message = BytesParser(policy=policy.compat32).parsebytes(metadata.read_bytes())
    except (OSError, UnicodeError):
        raise RuntimeWheelError("bootstrap METADATA 无法解析") from None
    names = message.get_all("Name", [])
    if len(names) != 1 or not isinstance(names[0], str):
        raise RuntimeWheelError("bootstrap 分发身份无效")
    normalized = re.sub(r"[-_.]+", "-", names[0]).lower()
    prefix = normalized.replace("-", "_") + "-"
    if not dist_info.name.lower().startswith(prefix):
        raise RuntimeWheelError("bootstrap 分发目录与身份不一致")
    return normalized


def _installed_record(dist_info: Path, purelib: Path) -> dict[str, _InstalledRecordFile]:
    record = _regular_file(dist_info / "RECORD", "bootstrap RECORD")
    try:
        if record.stat().st_size > _MAX_MEMBER_BYTES:
            raise RuntimeWheelError("bootstrap RECORD 过大")
        rows = csv.reader(io.StringIO(record.read_text(encoding="utf-8"), newline=""))
        entries = _installed_record_rows(rows)
    except (OSError, UnicodeError, csv.Error):
        raise RuntimeWheelError("bootstrap RECORD 无法解析") from None
    record_relative = record.relative_to(purelib).as_posix()
    if record_relative not in entries:
        raise RuntimeWheelError("bootstrap RECORD 缺少自身条目")
    _require_proven_unhashed_entries(entries, record_relative)
    return entries


def _installed_record_rows(rows: csv.reader) -> dict[str, _InstalledRecordFile]:
    entries: dict[str, _InstalledRecordFile] = {}
    for row_number, row in enumerate(rows, start=1):
        if row_number > _MAX_INSTALLED_RECORD_ROWS:
            raise RuntimeWheelError("bootstrap RECORD 行数超过安全预算")
        if len(row) != 3:
            raise RuntimeWheelError("bootstrap RECORD 行格式无效")
        relative = _normalized_installed_record_path(row[0])
        if relative is None:
            continue
        if relative in entries:
            raise RuntimeWheelError("bootstrap RECORD 路径重复")
        algorithm, digest = _installed_record_digest(row[1])
        size = _optional_record_size(row[2])
        entries[relative] = _InstalledRecordFile(relative, algorithm, digest, size)
    return entries


def _normalized_installed_record_path(value: str) -> str | None:
    normalized_input = value.replace("\\", "/")
    if not normalized_input or "\x00" in normalized_input or normalized_input.startswith("/"):
        raise RuntimeWheelError("bootstrap RECORD 路径无效")
    normalized = posixpath.normpath(normalized_input)
    if normalized == ".." or normalized.startswith("../"):
        return None
    path = PurePosixPath(normalized)
    if normalized != normalized_input or any(":" in part for part in path.parts):
        raise RuntimeWheelError("bootstrap RECORD 路径未规范化")
    return path.as_posix()


def _installed_record_digest(value: str) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    algorithm, separator, encoded = value.partition("=")
    if (
        separator != "="
        or algorithm not in hashlib.algorithms_guaranteed
        or algorithm in {"md5", "sha1"}
        or hashlib.new(algorithm).digest_size <= 0
    ):
        raise RuntimeWheelError("bootstrap RECORD 哈希算法无效")
    try:
        digest = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, binascii.Error):
        raise RuntimeWheelError("bootstrap RECORD 哈希编码无效") from None
    if len(digest) != hashlib.new(algorithm).digest_size:
        raise RuntimeWheelError("bootstrap RECORD 哈希长度无效")
    return algorithm, digest.hex()


def _optional_record_size(value: str) -> int | None:
    if not value:
        return None
    if len(value) > 20 or not value.isascii() or not value.isdecimal():
        raise RuntimeWheelError("bootstrap RECORD 大小无效")
    return int(value)


def _require_proven_unhashed_entries(
    entries: dict[str, _InstalledRecordFile],
    record_relative: str,
) -> None:
    for relative, entry in entries.items():
        if entry.digest is not None or relative == record_relative:
            continue
        source = _pyc_source(relative)
        if source is None or source not in entries or entries[source].digest is None:
            raise RuntimeWheelError("bootstrap RECORD 含无法证明的空哈希文件")


def _pyc_source(relative: str) -> str | None:
    path = PurePosixPath(relative)
    if path.suffix != ".pyc":
        return None
    if path.parent.name == "__pycache__":
        stem = path.name.partition(".")[0]
        return (path.parent.parent / f"{stem}.py").as_posix()
    return path.with_suffix(".py").as_posix()


def _require_known_directories(directories: set[str], allowed_files: set[str]) -> None:
    allowed_directories = {
        parent.as_posix()
        for name in allowed_files
        for parent in PurePosixPath(name).parents
        if parent.as_posix() != "."
    }
    if not directories.issubset(allowed_directories):
        raise RuntimeWheelError("版本 purelib 包含未知目录")


def _verify_application_files(
    actual: dict[str, Path],
    expected: dict[str, PayloadFile],
) -> None:
    for name, item in expected.items():
        path = actual[name]
        try:
            if path.stat().st_size != item.size or _file_sha256(path) != item.sha256:
                raise RuntimeWheelError("已安装应用源码内容与 wheel 不一致")
        except OSError:
            raise RuntimeWheelError("已安装应用源码不可读") from None


def _verify_bootstrap_files(
    actual: dict[str, Path],
    expected: dict[str, _InstalledRecordFile],
) -> None:
    for name, item in expected.items():
        path = actual.get(name)
        if path is None:
            raise RuntimeWheelError("bootstrap RECORD 声明文件缺失")
        if item.size is not None and path.stat().st_size != item.size:
            raise RuntimeWheelError("bootstrap RECORD 文件大小不一致")
        if item.algorithm is not None and _file_digest(path, item.algorithm) != item.digest:
            raise RuntimeWheelError("bootstrap RECORD 文件哈希不一致")


def _regular_file(path: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError:
        raise RuntimeWheelError(f"{label}不可用") from None
    if path.is_symlink():
        raise RuntimeWheelError(f"{label}不能是符号链接")
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeWheelError(f"{label}必须是普通文件")
    return path


def _plain_directory(path: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError:
        raise RuntimeWheelError(f"{label}不可用") from None
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
        raise RuntimeWheelError(f"{label}必须是非链接目录")
    return path


def _file_sha256(path: Path) -> str:
    return _file_digest(path, "sha256")


def _file_digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


__all__ = [
    "RuntimeWheelError",
    "WheelPayloadProof",
    "inspect_application_wheel",
    "verify_installed_application",
]
