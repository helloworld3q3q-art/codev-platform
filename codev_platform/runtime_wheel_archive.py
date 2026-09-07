"""在不执行归档代码的前提下验证应用 wheel。"""

from __future__ import annotations

import base64
import binascii
import csv
from email import policy
from email.parser import BytesParser
import hashlib
import io
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import zipfile

from codev_platform.runtime_wheel_contract import (
    PayloadFile,
    RuntimeWheelError,
    WheelPayloadProof,
)


# 应用分发可安装的顶层包必须是这个固定集合。`platform_meta` 是本项目
# wheel 内置的只读项目登记表资源；不得把这里改成任意顶层包的通配规则。
_APPLICATION_PACKAGE_PREFIXES = ("codev_platform/", "platform_meta/")
_MAX_ARCHIVE_MEMBERS = 20_000
_MAX_ARCHIVE_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
_MAX_COMPRESSION_RATIO = 200
_COMPRESSION_RATIO_MIN_BYTES = 1024 * 1024
_MAX_MEMBER_BYTES = 32 * 1024 * 1024
_MAX_PACKAGE_BYTES = 256 * 1024 * 1024
_MAX_RECORD_ROWS = _MAX_ARCHIVE_MEMBERS
_MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
_MAX_CENTRAL_DIRECTORY_BYTES = 32 * 1024 * 1024
_EOCD_SIGNATURE = b"PK\x05\x06"
_CENTRAL_SIGNATURE = b"PK\x01\x02"
_EOCD_SIZE = 22
_MAX_ZIP_COMMENT_BYTES = 65_535
_CENTRAL_HEADER_SIZE = 46
_ZIP16_SENTINEL = 0xFFFF
_ZIP32_SENTINEL = 0xFFFFFFFF


def inspect_application_wheel(wheel: Path) -> WheelPayloadProof:
    """不执行 wheel 代码，证明其只会向 purelib 安装受管应用。"""
    wheel_path = _regular_file(Path(wheel), "应用 wheel")
    try:
        _preflight_central_directory(wheel_path)
        with zipfile.ZipFile(wheel_path) as archive:
            members = _regular_members(archive)
            dist_info = _application_dist_info(members)
            _require_purelib_wheel(archive, members, dist_info)
            recorded = _verified_wheel_record(archive, members, dist_info)
            application = _application_payload(members, recorded, dist_info)
    except RuntimeWheelError:
        raise
    except (OSError, UnicodeError, zipfile.BadZipFile, RuntimeError):
        raise RuntimeWheelError("应用 wheel 无法安全读取") from None
    return WheelPayloadProof(
        wheel_path=wheel_path,
        wheel_sha256=_file_sha256(wheel_path),
        dist_info_relative=dist_info,
        application_files=application,
    )


def _preflight_central_directory(path: Path) -> None:
    """在 ``zipfile`` 分配 ZipInfo 前流式校验中央目录预算。"""
    try:
        file_size = path.stat().st_size
        if file_size < _EOCD_SIZE or file_size > _MAX_ARCHIVE_BYTES:
            raise RuntimeWheelError("应用 wheel 归档大小超过安全预算")
        with path.open("rb") as stream:
            eocd_offset, fields = _read_eocd(stream, file_size)
            (
                disk_number,
                central_disk,
                disk_entries,
                total_entries,
                central_size,
                central_offset,
            ) = fields
            if disk_number != 0 or central_disk != 0 or disk_entries != total_entries:
                raise RuntimeWheelError("应用 wheel 不接受多磁盘 ZIP")
            if (
                total_entries == _ZIP16_SENTINEL
                or central_size == _ZIP32_SENTINEL
                or central_offset == _ZIP32_SENTINEL
            ):
                raise RuntimeWheelError("应用 wheel 不接受 ZIP64")
            if total_entries > _MAX_ARCHIVE_MEMBERS:
                raise RuntimeWheelError("应用 wheel 成员数量超过安全预算")
            if central_size > _MAX_CENTRAL_DIRECTORY_BYTES:
                raise RuntimeWheelError("应用 wheel 中央目录超过安全预算")
            if central_offset + central_size != eocd_offset:
                raise RuntimeWheelError("应用 wheel 中央目录边界无效")
            _scan_central_directory(
                stream,
                offset=central_offset,
                size=central_size,
                entries=total_entries,
            )
    except RuntimeWheelError:
        raise
    except (OSError, struct.error):
        raise RuntimeWheelError("应用 wheel 中央目录不可安全读取") from None


def _read_eocd(stream, file_size: int) -> tuple[int, tuple[int, ...]]:
    tail_size = min(file_size, _EOCD_SIZE + _MAX_ZIP_COMMENT_BYTES)
    stream.seek(file_size - tail_size)
    tail = stream.read(tail_size)
    index = tail.rfind(_EOCD_SIGNATURE)
    if index < 0 or index + _EOCD_SIZE > len(tail):
        raise RuntimeWheelError("应用 wheel 缺少有效中央目录")
    unpacked = struct.unpack_from("<4s4H2LH", tail, index)
    comment_size = unpacked[-1]
    if index + _EOCD_SIZE + comment_size != len(tail):
        raise RuntimeWheelError("应用 wheel EOCD 边界无效")
    return file_size - tail_size + index, tuple(unpacked[1:-1])


def _scan_central_directory(
    stream,
    *,
    offset: int,
    size: int,
    entries: int,
) -> None:
    stream.seek(offset)
    consumed = 0
    for _entry in range(entries):
        header = stream.read(_CENTRAL_HEADER_SIZE)
        if len(header) != _CENTRAL_HEADER_SIZE:
            raise RuntimeWheelError("应用 wheel 中央目录条目不完整")
        fields = struct.unpack("<4s6H3L5H2L", header)
        if fields[0] != _CENTRAL_SIGNATURE:
            raise RuntimeWheelError("应用 wheel 中央目录签名无效")
        compressed_size, uncompressed_size = fields[8], fields[9]
        name_size, extra_size, comment_size = fields[10:13]
        disk_number, local_offset = fields[13], fields[16]
        variable_size = name_size + extra_size + comment_size
        consumed += _CENTRAL_HEADER_SIZE + variable_size
        if (
            disk_number != 0
            or compressed_size == _ZIP32_SENTINEL
            or uncompressed_size == _ZIP32_SENTINEL
            or local_offset == _ZIP32_SENTINEL
        ):
            raise RuntimeWheelError("应用 wheel 不接受 ZIP64 或多磁盘成员")
        if consumed > size or local_offset >= offset:
            raise RuntimeWheelError("应用 wheel 中央目录成员边界无效")
        variable = stream.read(variable_size)
        if len(variable) != variable_size:
            raise RuntimeWheelError("应用 wheel 中央目录成员不完整")
        _reject_zip64_extra(variable[name_size : name_size + extra_size])
    if consumed != size or stream.tell() != offset + size:
        raise RuntimeWheelError("应用 wheel 中央目录数量或大小不一致")


def _reject_zip64_extra(extra: bytes) -> None:
    offset = 0
    while offset < len(extra):
        if len(extra) - offset < 4:
            raise RuntimeWheelError("应用 wheel ZIP extra 字段无效")
        field_id, field_size = struct.unpack_from("<HH", extra, offset)
        offset += 4
        if offset + field_size > len(extra):
            raise RuntimeWheelError("应用 wheel ZIP extra 字段无效")
        if field_id == 0x0001:
            raise RuntimeWheelError("应用 wheel 不接受 ZIP64")
        offset += field_size


def _regular_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > _MAX_ARCHIVE_MEMBERS:
        raise RuntimeWheelError("应用 wheel 成员数量超过安全预算")
    members: dict[str, zipfile.ZipInfo] = {}
    seen: set[str] = set()
    total_size = 0
    total_compressed = 0
    for member in infos:
        name = _safe_member_name(member)
        if name in seen:
            raise RuntimeWheelError("应用 wheel 含重复成员")
        seen.add(name)
        if member.is_dir():
            if member.file_size != 0:
                raise RuntimeWheelError("应用 wheel 目录成员大小无效")
            continue
        _require_member_budget(member)
        total_size += member.file_size
        total_compressed += member.compress_size
        if total_size > _MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise RuntimeWheelError("应用 wheel 解压总量超过安全预算")
        members[name] = member
    _require_compression_budget(total_size, total_compressed)
    return members


def _require_member_budget(member: zipfile.ZipInfo) -> None:
    if member.file_size < 0 or member.compress_size < 0:
        raise RuntimeWheelError("应用 wheel 成员大小无效")
    if member.file_size > _MAX_MEMBER_BYTES:
        raise RuntimeWheelError("应用 wheel 成员大小超过安全预算")
    _require_compression_budget(member.file_size, member.compress_size)


def _require_compression_budget(uncompressed: int, compressed: int) -> None:
    if uncompressed < _COMPRESSION_RATIO_MIN_BYTES:
        return
    if compressed == 0 or uncompressed > compressed * _MAX_COMPRESSION_RATIO:
        raise RuntimeWheelError("应用 wheel 压缩比超过安全预算")


def _application_dist_info(members: dict[str, zipfile.ZipInfo]) -> str:
    roots = {
        name.partition("/")[0]
        for name in members
        if name.partition("/")[0].endswith(".dist-info")
    }
    if len(roots) != 1:
        raise RuntimeWheelError("应用 wheel 分发元数据目录不唯一")
    dist_info = roots.pop()
    if not dist_info.startswith("codev_platform-"):
        raise RuntimeWheelError("应用 wheel 分发身份不是 codev-platform")
    for required in ("METADATA", "WHEEL", "RECORD"):
        if f"{dist_info}/{required}" not in members:
            raise RuntimeWheelError("应用 wheel 分发元数据不完整")
    return dist_info


def _require_purelib_wheel(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    dist_info: str,
) -> None:
    content = _zip_member_bytes(archive, members[f"{dist_info}/WHEEL"])
    metadata = _zip_member_bytes(archive, members[f"{dist_info}/METADATA"])
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeError:
        raise RuntimeWheelError("应用 wheel WHEEL 元数据无效") from None
    names = BytesParser(policy=policy.compat32).parsebytes(metadata).get_all("Name", [])
    declared_name = names[0] if len(names) == 1 and isinstance(names[0], str) else ""
    if re.sub(r"[-_.]+", "-", declared_name).lower() != "codev-platform":
        raise RuntimeWheelError("应用 wheel 分发身份不是 codev-platform")
    declarations = [
        line.partition(":")[2].strip().lower()
        for line in lines
        if line.partition(":")[0].strip().lower() == "root-is-purelib"
    ]
    if declarations != ["true"]:
        raise RuntimeWheelError("应用 wheel 必须明确安装到 purelib")


def _verified_wheel_record(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    dist_info: str,
) -> dict[str, tuple[str, int]]:
    record_name = f"{dist_info}/RECORD"
    content = _zip_member_bytes(archive, members[record_name])
    try:
        rows = csv.reader(io.StringIO(content.decode("utf-8"), newline=""))
        entries = _record_entries(rows, record_name)
    except (csv.Error, UnicodeError):
        raise RuntimeWheelError("应用 wheel RECORD 无效") from None
    if set(entries) != set(members):
        raise RuntimeWheelError("应用 wheel RECORD 与归档成员不一致")
    for name, member in members.items():
        digest, size = entries[name]
        if name == record_name:
            if digest or size != -1:
                raise RuntimeWheelError("应用 wheel RECORD 自身条目无效")
            continue
        if size != member.file_size or digest != _zip_member_sha256(archive, member):
            raise RuntimeWheelError("应用 wheel RECORD 摘要或大小不一致")
    return entries


def _record_entries(
    rows: csv.reader,
    record_name: str,
) -> dict[str, tuple[str, int]]:
    entries: dict[str, tuple[str, int]] = {}
    for row_number, row in enumerate(rows, start=1):
        if row_number > _MAX_RECORD_ROWS:
            raise RuntimeWheelError("应用 wheel RECORD 行数超过安全预算")
        if len(row) != 3:
            raise RuntimeWheelError("应用 wheel RECORD 行格式无效")
        name, encoded_digest, size_text = row
        if not _safe_record_name(name) or name in entries:
            raise RuntimeWheelError("应用 wheel RECORD 路径无效或重复")
        if name == record_name:
            entries[name] = (encoded_digest, -1 if not size_text else _record_size(size_text))
            continue
        entries[name] = (_record_sha256(encoded_digest), _record_size(size_text))
    return entries


def _safe_record_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(
        name
        and "\\" not in name
        and "\x00" not in name
        and not path.is_absolute()
        and path.as_posix() == name
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _record_sha256(value: str) -> str:
    algorithm, separator, encoded = value.partition("=")
    if algorithm != "sha256" or separator != "=" or not encoded:
        raise RuntimeWheelError("应用 wheel RECORD 必须使用 sha256")
    try:
        digest = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error):
        raise RuntimeWheelError("应用 wheel RECORD 摘要编码无效") from None
    if len(digest) != hashlib.sha256().digest_size:
        raise RuntimeWheelError("应用 wheel RECORD 摘要长度无效")
    return digest.hex()


def _record_size(value: str) -> int:
    if not value or len(value) > 20 or not value.isascii() or not value.isdecimal():
        raise RuntimeWheelError("应用 wheel RECORD 大小无效")
    size = int(value)
    if size > _MAX_MEMBER_BYTES:
        raise RuntimeWheelError("应用 wheel RECORD 成员过大")
    return size


def _application_payload(
    members: dict[str, zipfile.ZipInfo],
    recorded: dict[str, tuple[str, int]],
    dist_info: str,
) -> tuple[PayloadFile, ...]:
    data_root = f"{dist_info.removesuffix('.dist-info')}.data/"
    payload: dict[str, PayloadFile] = {}
    total_size = 0
    for name in members:
        installed = _installed_application_path(name, dist_info, data_root)
        if installed is None:
            continue
        if installed in payload:
            raise RuntimeWheelError("应用 wheel purelib 安装目标重复")
        digest, size = recorded[name]
        total_size += size
        if total_size > _MAX_PACKAGE_BYTES:
            raise RuntimeWheelError("应用 wheel 源码总量过大")
        payload[installed] = PayloadFile(name, installed, digest, size)
    if "codev_platform/__init__.py" not in payload:
        raise RuntimeWheelError("应用 wheel 缺少主包源码")
    return tuple(payload[name] for name in sorted(payload))


def _installed_application_path(
    name: str,
    dist_info: str,
    data_root: str,
) -> str | None:
    if name.startswith(f"{dist_info}/"):
        return None
    if name.startswith(data_root):
        relative = name.removeprefix(data_root)
        if not relative.startswith("purelib/"):
            raise RuntimeWheelError("应用 wheel .data 含非 purelib 载荷")
        installed = relative.removeprefix("purelib/")
    else:
        installed = name
    if not _is_allowed_application_payload(installed):
        raise RuntimeWheelError("应用 wheel purelib 含 codev_platform 外载荷")
    return installed


def _is_allowed_application_payload(installed: str) -> bool:
    """只接受本项目分发契约中精确声明的两个包根。"""
    return any(installed.startswith(prefix) for prefix in _APPLICATION_PACKAGE_PREFIXES)


def _zip_member_bytes(archive: zipfile.ZipFile, member: zipfile.ZipInfo) -> bytes:
    if member.file_size > _MAX_MEMBER_BYTES:
        raise RuntimeWheelError("应用 wheel 元数据成员过大")
    with archive.open(member) as stream:
        content = stream.read(_MAX_MEMBER_BYTES + 1)
    if len(content) != member.file_size or len(content) > _MAX_MEMBER_BYTES:
        raise RuntimeWheelError("应用 wheel 元数据成员大小漂移")
    return content


def _safe_member_name(member: zipfile.ZipInfo) -> str:
    name = member.filename
    path = PurePosixPath(name)
    unix_mode = member.external_attr >> 16
    if (
        not name
        or "\\" in name
        or "\x00" in name
        or path.is_absolute()
        or path.as_posix() != name.rstrip("/")
        or any(part in {"", ".", ".."} for part in path.parts)
        or stat.S_ISLNK(unix_mode)
    ):
        raise RuntimeWheelError("应用 wheel 成员路径无效")
    return name.rstrip("/")


def _zip_member_sha256(archive: zipfile.ZipFile, member: zipfile.ZipInfo) -> str:
    digest = hashlib.sha256()
    read_size = 0
    with archive.open(member) as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            read_size += len(block)
            if read_size > member.file_size or read_size > _MAX_MEMBER_BYTES:
                raise RuntimeWheelError("应用 wheel 源码成员大小漂移")
            digest.update(block)
    if read_size != member.file_size:
        raise RuntimeWheelError("应用 wheel 源码成员不完整")
    return digest.hexdigest()


def _regular_file(path: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError:
        raise RuntimeWheelError(f"{label}不可用") from None
    if path.is_symlink():
        raise RuntimeWheelError(f"{label}不能是符号链接")
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeWheelError(f"{label}必须是普通文件")
    if metadata.st_size > _MAX_ARCHIVE_BYTES:
        raise RuntimeWheelError(f"{label}大小超过安全预算")
    return path


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


__all__ = ["inspect_application_wheel"]
