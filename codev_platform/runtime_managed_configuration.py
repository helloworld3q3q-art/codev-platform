"""机器配置的 root 暂存与维护窗口内原子发布。"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from codev_platform.core.systemd_environment_file import (
    SystemdEnvironmentFileError,
    parse_systemd_environment_keys,
    validate_systemd_environment_file,
)
from codev_platform.runtime_deployment_contract import DeploymentPlan, RuntimeDeploymentError


MANAGED_CONFIG_PATH = Path("/etc/codev-platform/config.json")
MANAGED_ENVIRONMENT_PATH = Path("/etc/codev-platform/platform.env")
MAX_MANAGED_CONFIG_BYTES = 1024 * 1024
_MAX_CONFIG_BYTES = MAX_MANAGED_CONFIG_BYTES
_MAX_ENVIRONMENT_BYTES = 128 * 1024
_STAGED_MODE = 0o600
_PUBLISHED_MODE = 0o640


@dataclass(frozen=True, slots=True)
class ManagedConfigurationPayload:
    config: bytes
    environment: bytes
    config_sha256: str
    environment_sha256: str
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class ManagedConfigurationReceipt:
    config_sha256: str
    environment_sha256: str
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class ManagedConfigurationBootstrapReceipt:
    """一次性受管配置引导的无秘密回执。"""

    config_sha256: str
    environment_sha256: str
    evidence_sha256: str
    state: str


@dataclass(frozen=True, slots=True)
class _BootstrapPublicationPlan:
    """在维护锁内冻结的最小两文件发布决策。"""

    payload: ManagedConfigurationPayload
    group_id: int
    environment_before: bytes
    publish_config: bool
    publish_environment: bool


def prepare_managed_configuration(
    config: bytes,
    environment: bytes,
) -> ManagedConfigurationPayload:
    """规范化非秘密机器配置；返回值只应进入受保护文件，不能写日志。"""
    if type(config) is not bytes or not config or len(config) > _MAX_CONFIG_BYTES:
        raise RuntimeDeploymentError("机器配置大小无效")
    if type(environment) is not bytes or len(environment) > _MAX_ENVIRONMENT_BYTES:
        raise RuntimeDeploymentError("systemd 环境配置大小无效")
    normalized_config = _normalize_config(config)
    normalized_environment = _normalize_environment(environment)
    config_digest = hashlib.sha256(normalized_config).hexdigest()
    environment_digest = hashlib.sha256(normalized_environment).hexdigest()
    evidence = hashlib.sha256(
        f"{config_digest}\n{environment_digest}\n".encode("ascii")
    ).hexdigest()
    return ManagedConfigurationPayload(
        normalized_config,
        normalized_environment,
        config_digest,
        environment_digest,
        evidence,
    )


def stage_managed_configuration(plan: DeploymentPlan) -> ManagedConfigurationReceipt:
    """在停写前冻结配置快照，但不改变当前服务读取的 `/etc` 文件。"""
    _require_root()
    config = _read_snapshot(Path(plan.config_source), _MAX_CONFIG_BYTES)
    environment = _read_snapshot(Path(plan.environment_source), _MAX_ENVIRONMENT_BYTES)
    payload = prepare_managed_configuration(config, environment)
    directory = _staging_directory(plan)
    _ensure_private_directory(directory)
    _atomic_write(directory / "config.json", payload.config, _STAGED_MODE, 0)
    _atomic_write(directory / "platform.env", payload.environment, _STAGED_MODE, 0)
    metadata = _metadata_bytes(plan, payload)
    _atomic_write(directory / "metadata.json", metadata, _STAGED_MODE, 0)
    return _receipt(payload)


def verify_staged_configuration(plan: DeploymentPlan) -> ManagedConfigurationReceipt:
    """续跑只接受与计划摘要、目标提交及两份内容摘要完全一致的暂存。"""
    _require_root()
    directory = _staging_directory(plan)
    _require_private_directory(directory)
    config = _read_root_file(directory / "config.json", _MAX_CONFIG_BYTES, _STAGED_MODE)
    environment = _read_root_file(
        directory / "platform.env",
        _MAX_ENVIRONMENT_BYTES,
        _STAGED_MODE,
    )
    payload = prepare_managed_configuration(config, environment)
    metadata = _read_root_file(directory / "metadata.json", 16 * 1024, _STAGED_MODE)
    if metadata != _metadata_bytes(plan, payload):
        raise RuntimeDeploymentError("暂存机器配置元数据不一致")
    return _receipt(payload)


def publish_staged_configuration(plan: DeploymentPlan) -> ManagedConfigurationReceipt:
    """维护窗口内把已冻结快照发布到 root 管理位置，并立即复验。"""
    staged = verify_staged_configuration(plan)
    directory = MANAGED_CONFIG_PATH.parent
    group_id = _service_group_id(plan.service_user)
    _ensure_published_directory(directory, group_id)
    source = _staging_directory(plan)
    config = _read_root_file(source / "config.json", _MAX_CONFIG_BYTES, _STAGED_MODE)
    environment = _read_root_file(source / "platform.env", _MAX_ENVIRONMENT_BYTES, _STAGED_MODE)
    _atomic_write(MANAGED_CONFIG_PATH, config, _PUBLISHED_MODE, group_id)
    _atomic_write(MANAGED_ENVIRONMENT_PATH, environment, _PUBLISHED_MODE, group_id)
    actual = verify_published_configuration(plan)
    if actual != staged:
        raise RuntimeDeploymentError("发布机器配置与暂存快照不一致")
    return actual


def verify_published_configuration(plan: DeploymentPlan) -> ManagedConfigurationReceipt:
    """证明服务账号可读的两份文件仍由 root 管理且内容与暂存一致。"""
    _require_root()
    group_id = _service_group_id(plan.service_user)
    config = _read_published_file(MANAGED_CONFIG_PATH, _MAX_CONFIG_BYTES, group_id)
    environment = _read_published_file(
        MANAGED_ENVIRONMENT_PATH,
        _MAX_ENVIRONMENT_BYTES,
        group_id,
    )
    try:
        validate_systemd_environment_file(MANAGED_ENVIRONMENT_PATH)
    except SystemdEnvironmentFileError:
        raise RuntimeDeploymentError("发布 systemd 环境文件不受信任") from None
    actual = _receipt(prepare_managed_configuration(config, environment))
    expected = verify_staged_configuration(plan)
    if actual != expected:
        raise RuntimeDeploymentError("发布机器配置发生漂移")
    return actual


def bootstrap_missing_managed_configuration(
    config: bytes,
    *,
    service_user: str,
    apply: bool,
) -> ManagedConfigurationBootstrapReceipt:
    """仅补齐缺失的 root 受管配置；相同的中断半态可安全续跑。"""
    _require_root()
    if type(service_user) is not str or not service_user or type(apply) is not bool:
        raise RuntimeDeploymentError("受管配置引导参数无效")
    publication = _prepare_bootstrap_publication(config, service_user)
    if not apply:
        return _bootstrap_receipt(
            publication.payload,
            "ready"
            if publication.publish_config or publication.publish_environment
            else "already_published",
        )
    if not publication.publish_config and not publication.publish_environment:
        return _bootstrap_receipt(publication.payload, "already_published")
    _publish_bootstrap_publication(publication)
    _verify_bootstrap_publication(publication)
    return _bootstrap_receipt(publication.payload, "published")


def _normalize_config(content: bytes) -> bytes:
    try:
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise RuntimeDeploymentError("机器配置 JSON 无法严格解码") from None
    if type(value) is not dict:
        raise RuntimeDeploymentError("机器配置根必须是 JSON 对象")
    runtime = value.get("runtime")
    systemd = value.get("systemd")
    if runtime is not None and type(runtime) is not dict:
        raise RuntimeDeploymentError("机器配置 runtime 节类型无效")
    if systemd is not None and type(systemd) is not dict:
        raise RuntimeDeploymentError("机器配置 systemd 节类型无效")
    value["runtime"] = dict(runtime or {})
    value["systemd"] = dict(systemd or {})
    value["runtime"]["release_root"] = "/var/lib/codev-platform/runtime"
    value["systemd"]["env_file"] = MANAGED_ENVIRONMENT_PATH.as_posix()
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _normalize_environment(content: bytes) -> bytes:
    try:
        text = content.decode("utf-8")
    except UnicodeError:
        raise RuntimeDeploymentError("systemd 环境配置编码无效") from None
    assignment = f"CODEV_PLATFORM_CONFIG={MANAGED_CONFIG_PATH.as_posix()}"
    lines = text.splitlines()
    matching = [line.strip() for line in lines if line.strip().startswith("CODEV_PLATFORM_CONFIG=")]
    if matching and matching != [assignment]:
        raise RuntimeDeploymentError("systemd 环境配置指向了非受管机器配置")
    if not matching:
        lines.append(assignment)
    normalized = ("\n".join(lines).rstrip("\n") + "\n").encode("utf-8")
    try:
        parse_systemd_environment_keys(normalized)
    except SystemdEnvironmentFileError:
        raise RuntimeDeploymentError("systemd 环境配置格式无效") from None
    return normalized


def _metadata_bytes(plan: DeploymentPlan, payload: ManagedConfigurationPayload) -> bytes:
    value = {
        "config_sha256": payload.config_sha256,
        "environment_sha256": payload.environment_sha256,
        "evidence_sha256": payload.evidence_sha256,
        "plan_sha256": plan.digest,
        "schema_version": 1,
        "target_revision": plan.target_revision,
    }
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("ascii")


def _receipt(payload: ManagedConfigurationPayload) -> ManagedConfigurationReceipt:
    return ManagedConfigurationReceipt(
        payload.config_sha256,
        payload.environment_sha256,
        payload.evidence_sha256,
    )


def _bootstrap_receipt(
    payload: ManagedConfigurationPayload,
    state: str,
) -> ManagedConfigurationBootstrapReceipt:
    return ManagedConfigurationBootstrapReceipt(
        payload.config_sha256,
        payload.environment_sha256,
        payload.evidence_sha256,
        state,
    )


def _prepare_bootstrap_publication(
    config: bytes,
    service_user: str,
) -> _BootstrapPublicationPlan:
    """只接受可信旧环境文件和缺失或完全相同的配置目标。"""
    group_id = _service_group_id(service_user)
    environment = _read_bootstrap_environment(group_id)
    payload = prepare_managed_configuration(config, environment)
    existing_config = _read_bootstrap_config(payload, group_id)
    publish_config = existing_config is None
    publish_environment = not hmac.compare_digest(environment, payload.environment)
    if publish_config and not publish_environment:
        raise RuntimeDeploymentError("受管配置引导状态不完整")
    return _BootstrapPublicationPlan(
        payload,
        group_id,
        environment,
        publish_config,
        publish_environment,
    )


def _read_bootstrap_environment(group_id: int) -> bytes:
    try:
        validate_systemd_environment_file(MANAGED_ENVIRONMENT_PATH)
    except SystemdEnvironmentFileError:
        raise RuntimeDeploymentError("受管 systemd 环境文件不受信任") from None
    return _read_bootstrap_environment_file(group_id)


def _read_bootstrap_environment_file(group_id: int) -> bytes:
    """兼容历史 root-only `0600`，但只允许发布后的服务组 `0640`。"""
    try:
        linked = MANAGED_ENVIRONMENT_PATH.lstat()
    except OSError as error:
        raise RuntimeDeploymentError("受管 systemd 环境文件不可用") from error
    mode = stat.S_IMODE(linked.st_mode)
    legacy_root_only = mode == _STAGED_MODE and linked.st_gid == 0
    published_service_group = mode == _PUBLISHED_MODE and linked.st_gid == group_id
    if (
        not stat.S_ISREG(linked.st_mode)
        or stat.S_ISLNK(linked.st_mode)
        or linked.st_uid != 0
        or linked.st_nlink != 1
        or linked.st_size > _MAX_ENVIRONMENT_BYTES
        or not (legacy_root_only or published_service_group)
    ):
        raise RuntimeDeploymentError("受管 systemd 环境文件权限不受信任")
    return _read_fd_snapshot(
        MANAGED_ENVIRONMENT_PATH,
        _MAX_ENVIRONMENT_BYTES,
        linked=linked,
    )


def _read_bootstrap_config(
    payload: ManagedConfigurationPayload,
    group_id: int,
) -> bytes | None:
    if MANAGED_CONFIG_PATH.is_symlink():
        raise RuntimeDeploymentError("受管机器配置目标不安全")
    if not MANAGED_CONFIG_PATH.exists():
        return None
    existing = _read_published_file(MANAGED_CONFIG_PATH, _MAX_CONFIG_BYTES, group_id)
    if not hmac.compare_digest(existing, payload.config):
        raise RuntimeDeploymentError("受管机器配置目标已存在且内容不一致")
    return existing


def _publish_bootstrap_publication(publication: _BootstrapPublicationPlan) -> None:
    """按“配置先于引用”的顺序发布；失败后的同内容半态可以续跑。"""
    _ensure_published_directory(MANAGED_CONFIG_PATH.parent, publication.group_id)
    _require_bootstrap_environment_unchanged(publication)
    if publication.publish_config:
        _atomic_write(
            MANAGED_CONFIG_PATH,
            publication.payload.config,
            _PUBLISHED_MODE,
            publication.group_id,
        )
    _require_bootstrap_config_matches(publication)
    _require_bootstrap_environment_unchanged(publication)
    if publication.publish_environment:
        _atomic_write(
            MANAGED_ENVIRONMENT_PATH,
            publication.payload.environment,
            _PUBLISHED_MODE,
            publication.group_id,
        )


def _require_bootstrap_environment_unchanged(
    publication: _BootstrapPublicationPlan,
) -> None:
    current = _read_bootstrap_environment(publication.group_id)
    if not hmac.compare_digest(current, publication.environment_before):
        raise RuntimeDeploymentError("受管 systemd 环境文件在引导期间变化")


def _require_bootstrap_config_matches(publication: _BootstrapPublicationPlan) -> None:
    current = _read_bootstrap_config(publication.payload, publication.group_id)
    if current is None:
        raise RuntimeDeploymentError("受管机器配置引导未完成")


def _verify_bootstrap_publication(publication: _BootstrapPublicationPlan) -> None:
    config = _read_published_file(
        MANAGED_CONFIG_PATH,
        _MAX_CONFIG_BYTES,
        publication.group_id,
    )
    environment = _read_bootstrap_environment(publication.group_id)
    actual = _receipt(prepare_managed_configuration(config, environment))
    expected = _receipt(publication.payload)
    if actual != expected or not hmac.compare_digest(
        environment,
        publication.payload.environment,
    ):
        raise RuntimeDeploymentError("受管机器配置引导发布证明不一致")


def _staging_directory(plan: DeploymentPlan) -> Path:
    return Path(plan.runtime_root) / "deployments" / "config" / plan.target_revision


def _read_snapshot(path: Path, maximum: int) -> bytes:
    try:
        linked = path.lstat()
    except OSError as error:
        raise RuntimeDeploymentError("机器配置源不可用") from error
    if (
        not path.is_absolute()
        or not stat.S_ISREG(linked.st_mode)
        or stat.S_ISLNK(linked.st_mode)
        or linked.st_nlink != 1
        or linked.st_size > maximum
    ):
        raise RuntimeDeploymentError("机器配置源不受信任")
    return _read_fd_snapshot(path, maximum, linked=linked)


def _read_root_file(path: Path, maximum: int, mode: int) -> bytes:
    try:
        linked = path.lstat()
    except OSError as error:
        raise RuntimeDeploymentError("root 受管配置不可用") from error
    if (
        not stat.S_ISREG(linked.st_mode)
        or stat.S_ISLNK(linked.st_mode)
        or linked.st_uid != 0
        or stat.S_IMODE(linked.st_mode) != mode
        or linked.st_nlink != 1
        or linked.st_size > maximum
    ):
        raise RuntimeDeploymentError("root 受管配置元数据无效")
    return _read_fd_snapshot(path, maximum, linked=linked)


def _read_published_file(path: Path, maximum: int, group_id: int) -> bytes:
    content = _read_root_file(path, maximum, _PUBLISHED_MODE)
    if path.stat().st_gid != group_id:
        raise RuntimeDeploymentError("发布机器配置组身份无效")
    return content


def _read_fd_snapshot(path: Path, maximum: int, *, linked: os.stat_result) -> bytes:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if _identity(linked) != _identity(opened):
            raise RuntimeDeploymentError("机器配置读取前发生替换")
        blocks: list[bytes] = []
        total = 0
        while True:
            block = os.read(descriptor, min(8192, maximum + 1 - total))
            if not block:
                break
            blocks.append(block)
            total += len(block)
            if total > maximum:
                raise RuntimeDeploymentError("机器配置超出固定上限")
        after = os.fstat(descriptor)
        if _identity(opened) != _identity(after) or total != after.st_size:
            raise RuntimeDeploymentError("机器配置读取期间发生变化")
        return b"".join(blocks)
    except RuntimeDeploymentError:
        raise
    except OSError as error:
        raise RuntimeDeploymentError("机器配置无法安全读取") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _atomic_write(path: Path, content: bytes, mode: int, group_id: int) -> None:
    if path.exists() or path.is_symlink():
        existing = path.lstat()
        if (
            not stat.S_ISREG(existing.st_mode)
            or stat.S_ISLNK(existing.st_mode)
            or existing.st_uid != 0
            or stat.S_IMODE(existing.st_mode) & 0o022
        ):
            raise RuntimeDeploymentError("受管配置既有目标不安全")
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            mode,
        )
        os.fchown(descriptor, 0, group_id)
        os.fchmod(descriptor, mode)
        _write_all(descriptor, content)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except RuntimeDeploymentError:
        raise
    except OSError as error:
        raise RuntimeDeploymentError("受管配置原子发布失败") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    current = path
    while current.name in {path.name, "config", "deployments"}:
        metadata = current.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or metadata.st_uid != 0:
            raise RuntimeDeploymentError("配置暂存目录不受信任")
        if current.name in {path.name, "config"}:
            current.chmod(0o700)
        if current.name == "deployments":
            break
        current = current.parent


def _require_private_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise RuntimeDeploymentError("配置暂存目录不可用") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise RuntimeDeploymentError("配置暂存目录不受信任")


def _ensure_published_directory(path: Path, group_id: int) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o750)
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise RuntimeDeploymentError("受管配置目录不安全")
    os.chown(path, 0, group_id)
    path.chmod(0o750)
    _fsync_directory(path.parent)


def _service_group_id(user: str) -> int:
    try:
        import pwd

        account = pwd.getpwnam(user)
    except (ImportError, KeyError, OSError):
        raise RuntimeDeploymentError("服务账号不存在") from None
    return account.pw_gid


def _require_root() -> None:
    if os.name != "posix" or os.geteuid() != 0:
        raise RuntimeDeploymentError("机器配置发布要求 Linux root")


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise RuntimeDeploymentError("受管配置写入未取得进展")
        offset += written


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 包含重复字段")
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise ValueError("JSON 常量无效")


__all__ = [
    "MANAGED_CONFIG_PATH",
    "MANAGED_ENVIRONMENT_PATH",
    "MAX_MANAGED_CONFIG_BYTES",
    "ManagedConfigurationBootstrapReceipt",
    "ManagedConfigurationPayload",
    "ManagedConfigurationReceipt",
    "bootstrap_missing_managed_configuration",
    "prepare_managed_configuration",
    "publish_staged_configuration",
    "stage_managed_configuration",
    "verify_published_configuration",
    "verify_staged_configuration",
]
