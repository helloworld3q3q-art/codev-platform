"""CodeGraph 恢复前调用方与受管 unit 的配置同源证明。"""

from __future__ import annotations

import hmac
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from codev_platform.core.systemd_environment_file import (
    SystemdEnvironmentFileError,
    parse_systemd_environment_keys,
)
from codev_platform.ops.reindex_codegraph_resume_contract import (
    CODEGRAPH_RESUME_DROPIN,
    CODEGRAPH_UNIT,
    FORBIDDEN_ENVIRONMENT,
    PROTECTED_ENVIRONMENT,
    REINDEX_RESUME_DROPIN,
    REINDEX_UNIT,
    RESUME_ENVIRONMENT_FILE,
)


_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ENVIRONMENT_FILE = re.compile(r"(\S+)\s+\(ignore_errors=(yes|no)\)")
_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_SYSTEMCTL_TIMEOUT_SEC = 5.0
_MAX_ENVIRONMENT_BYTES = 32 * 1024
_MAX_ENVIRONMENT_FILE_BYTES = 128 * 1024
_RESUME_DROPINS = {
    REINDEX_UNIT: REINDEX_RESUME_DROPIN,
    CODEGRAPH_UNIT: CODEGRAPH_RESUME_DROPIN,
}
_CONFIG_OVERLAY_ENV = "CODEV_PLATFORM_CONFIG"
_EnvironmentFilesProofResult = TypeVar("_EnvironmentFilesProofResult")


class CodegraphResumeConfigurationError(RuntimeError):
    """调用方与受管服务无法证明使用同一配置快照。"""

    def __init__(
        self,
        message: str,
        *,
        unit: str | None = None,
        property_name: str | None = None,
        dropin_active: bool | None = None,
        environment_files_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.unit = unit
        self.property_name = property_name
        self.dropin_active = dropin_active
        self.environment_files_reason = environment_files_reason


CommandRunner = Callable[..., object]
EnvironmentFileReader = Callable[[Path], bytes]


def verify_codegraph_resume_configuration(
    *,
    config_path: Path,
    data_root: Path,
    config_digest: str,
    managed_environment_path: Path = RESUME_ENVIRONMENT_FILE,
    platform_name: str | None = None,
    command_runner: CommandRunner | None = None,
    environment_file_reader: EnvironmentFileReader | None = None,
) -> None:
    """要求两个固定 unit 只从同一受管快照取得受保护配置。"""
    expected_config = _normalize_config_path(config_path)
    expected_data_root = _normalize_absolute_path(data_root, "恢复数据根")
    expected_digest = _normalize_digest(config_digest)
    managed_path = _normalize_managed_environment_path(managed_environment_path)
    run = _require_linux_runner(platform_name, command_runner)
    read_file = (
        _default_environment_file_reader
        if environment_file_reader is None
        else environment_file_reader
    )
    if not callable(read_file):
        raise CodegraphResumeConfigurationError("恢复配置同源证明适配器不可用")
    for unit in (REINDEX_UNIT, CODEGRAPH_UNIT):
        _verify_unit(
            run=run,
            read_file=read_file,
            unit=unit,
            managed_path=managed_path,
            expected_config=expected_config,
            expected_data_root=expected_data_root,
            expected_digest=expected_digest,
        )


def _verify_unit(
    *,
    run: CommandRunner,
    read_file: EnvironmentFileReader,
    unit: str,
    managed_path: Path,
    expected_config: Path,
    expected_data_root: Path,
    expected_digest: str,
) -> None:
    _verify_proof_step(
        unit,
        "Environment",
        lambda: _require_static_environment_safe(_read_unit_property(run, unit, "Environment")),
    )
    _verify_environment_files(
        run=run,
        read_file=read_file,
        unit=unit,
        managed_path=managed_path,
        expected_config=expected_config,
        expected_data_root=expected_data_root,
        expected_digest=expected_digest,
    )
    _verify_proof_step(
        unit,
        "UnsetEnvironment",
        lambda: _require_environment_name_source_safe(
            _read_unit_property(run, unit, "UnsetEnvironment")
        ),
    )
    _verify_proof_step(
        unit,
        "PassEnvironment",
        lambda: _require_environment_name_source_safe(
            _read_unit_property(run, unit, "PassEnvironment")
        ),
    )
    _verify_proof_step(
        unit,
        "PAMName",
        lambda: _require_pam_name_empty(_read_unit_property(run, unit, "PAMName")),
    )


def _verify_environment_files(
    *,
    run: CommandRunner,
    read_file: EnvironmentFileReader,
    unit: str,
    managed_path: Path,
    expected_config: Path,
    expected_data_root: Path,
    expected_digest: str,
) -> None:
    files = _run_environment_files_proof_step(
        lambda: _parse_environment_files(_read_unit_property(run, unit, "EnvironmentFiles")),
        run=run,
        unit=unit,
        reason="format",
    )
    managed_file = _run_environment_files_proof_step(
        lambda: _select_managed_environment_file(files, managed_path=managed_path),
        run=run,
        unit=unit,
        reason="managed_reference",
    )
    _run_environment_files_proof_step(
        lambda: _require_managed_environment_snapshot(
            managed_file,
            expected_config=expected_config,
            expected_data_root=expected_data_root,
            expected_digest=expected_digest,
            read_file=read_file,
        ),
        run=run,
        unit=unit,
        reason="managed_snapshot",
    )
    _run_environment_files_proof_step(
        lambda: _require_auxiliary_environment_files_safe(
            files,
            managed_path=managed_path,
            expected_config=expected_config,
            read_file=read_file,
        ),
        run=run,
        unit=unit,
        reason="auxiliary",
    )


def _run_environment_files_proof_step(
    action: Callable[[], _EnvironmentFilesProofResult],
    *,
    run: CommandRunner,
    unit: str,
    reason: str,
) -> _EnvironmentFilesProofResult:
    """将固定子步骤的失败收敛为无秘密的环境文件原因码。"""
    try:
        return action()
    except CodegraphResumeConfigurationError as error:
        if error.unit is not None or error.property_name is not None:
            raise
        raise CodegraphResumeConfigurationError(
            str(error),
            unit=unit,
            property_name="EnvironmentFiles",
            dropin_active=_resume_dropin_active(run, unit),
            environment_files_reason=reason,
        ) from error


def _resume_dropin_active(run: CommandRunner, unit: str) -> bool | None:
    """读取失败时不掩盖原 proof 错误，只省略这项补充诊断。"""
    expected = _RESUME_DROPINS.get(unit)
    if expected is None:
        return None
    try:
        paths = _read_unit_property(run, unit, "DropInPaths")
    except CodegraphResumeConfigurationError:
        return None
    return any(Path(value) == expected for value in paths.split())


def _verify_proof_step(unit: str, property_name: str, action: Callable[[], None]) -> None:
    try:
        action()
    except CodegraphResumeConfigurationError as error:
        if error.unit is not None or error.property_name is not None:
            raise
        raise CodegraphResumeConfigurationError(
            str(error),
            unit=unit,
            property_name=property_name,
        ) from error


def _normalize_config_path(value: Path) -> Path:
    path = _normalize_absolute_path(value, "恢复配置覆盖")
    try:
        if not path.is_file():
            raise CodegraphResumeConfigurationError("恢复配置覆盖不可用")
    except CodegraphResumeConfigurationError:
        raise
    except OSError:
        raise CodegraphResumeConfigurationError("恢复配置覆盖不可用") from None
    return path


def _normalize_managed_environment_path(value: Path) -> Path:
    try:
        path = Path(value)
        text = path.as_posix()
        if (
            not path.is_absolute()
            or text.startswith("//")
            or ".." in path.parts
            or any(symbol in text for symbol in ("*", "?", "[", "]"))
        ):
            raise CodegraphResumeConfigurationError("受管恢复环境文件不可用")
        return path
    except CodegraphResumeConfigurationError:
        raise
    except (TypeError, ValueError):
        raise CodegraphResumeConfigurationError("受管恢复环境文件不可用") from None


def _normalize_absolute_path(value: Path, label: str) -> Path:
    try:
        path = Path(value)
        if not path.is_absolute():
            raise CodegraphResumeConfigurationError(f"{label}必须为绝对路径")
        return path.resolve()
    except CodegraphResumeConfigurationError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise CodegraphResumeConfigurationError(f"{label}不可用") from None


def _normalize_digest(value: object) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise CodegraphResumeConfigurationError("恢复配置摘要无效")
    return value


def _require_linux_runner(
    platform_name: str | None,
    command_runner: CommandRunner | None,
) -> CommandRunner:
    platform = sys.platform if platform_name is None else platform_name
    if type(platform) is not str or not platform.startswith("linux"):
        raise CodegraphResumeConfigurationError("当前平台无法证明恢复配置同源")
    run = _default_command_runner if command_runner is None else command_runner
    if not callable(run):
        raise CodegraphResumeConfigurationError("恢复配置同源证明适配器不可用")
    return run


def _read_unit_property(run: CommandRunner, unit: str, property_name: str) -> str:
    try:
        result = run(
            (
                "systemctl",
                "show",
                unit,
                f"--property={property_name}",
                "--value",
            ),
            timeout_sec=_SYSTEMCTL_TIMEOUT_SEC,
        )
    except MemoryError:
        raise
    except Exception as error:
        raise CodegraphResumeConfigurationError("无法读取受管服务配置环境") from error
    if getattr(result, "returncode", None) != 0 or type(getattr(result, "stdout", None)) is not str:
        raise CodegraphResumeConfigurationError("受管服务配置环境不可用")
    return result.stdout


def _require_static_environment_safe(raw: str) -> None:
    for name in _parse_environment_tokens(raw):
        if name in PROTECTED_ENVIRONMENT or name == FORBIDDEN_ENVIRONMENT:
            raise CodegraphResumeConfigurationError("受管服务静态环境覆盖恢复配置")


def _parse_environment_tokens(raw: str) -> set[str]:
    _require_plain_systemd_value(raw)
    names: set[str] = set()
    for token in raw.strip().split():
        name, separator, _value = token.partition("=")
        if not separator or _ENVIRONMENT_NAME.fullmatch(name) is None:
            raise CodegraphResumeConfigurationError("受管服务配置环境格式无效")
        if name in names:
            raise CodegraphResumeConfigurationError("受管服务配置环境格式无效")
        names.add(name)
    return names


def _parse_environment_files(raw: str) -> tuple[Path, ...]:
    _require_environment_files_systemd_value(raw)
    remaining = raw.strip()
    if not remaining:
        return ()
    paths: list[Path] = []
    while remaining:
        match = _ENVIRONMENT_FILE.match(remaining)
        if match is None or match.group(2) != "no":
            raise CodegraphResumeConfigurationError("受管服务环境文件格式无效")
        path = _parse_environment_file_path(match.group(1))
        if path in paths:
            raise CodegraphResumeConfigurationError("受管服务环境文件格式无效")
        paths.append(path)
        remaining = remaining[match.end() :].lstrip()
    return tuple(paths)


def _require_environment_files_systemd_value(raw: str) -> None:
    """允许 systemd 按行返回多个文件，同时保留其余语法门禁。"""
    if (
        type(raw) is not str
        or len(raw.encode("utf-8", "surrogatepass")) > _MAX_ENVIRONMENT_BYTES
        or "\x00" in raw
        or "\r" in raw
        or "\\" in raw
        or "'" in raw
        or '"' in raw
    ):
        raise CodegraphResumeConfigurationError("受管服务配置环境格式无效")


def _parse_environment_file_path(value: str) -> Path:
    """保留 systemd 配置的原始路径，交由可信读取叶子完成解析与读取。"""
    try:
        path = Path(value)
        if (
            not path.is_absolute()
            or value.startswith("//")
            or path.as_posix() != value
            or ".." in path.parts
            or any(symbol in value for symbol in ("*", "?", "[", "]"))
        ):
            raise CodegraphResumeConfigurationError("受管服务环境文件格式无效")
        return path
    except CodegraphResumeConfigurationError:
        raise
    except (TypeError, ValueError):
        raise CodegraphResumeConfigurationError("受管服务环境文件格式无效") from None


def _select_managed_environment_file(
    files: tuple[Path, ...],
    *,
    managed_path: Path,
) -> Path:
    managed_files = tuple(path for path in files if path == managed_path)
    if len(managed_files) != 1:
        raise CodegraphResumeConfigurationError("受管服务未精确引用恢复配置快照")
    return managed_files[0]


def _require_managed_environment_snapshot(
    managed_file: Path,
    *,
    expected_config: Path,
    expected_data_root: Path,
    expected_digest: str,
    read_file: EnvironmentFileReader,
) -> None:
    values = _parse_environment_file_content(_read_environment_file(managed_file, read_file))
    if set(values) != PROTECTED_ENVIRONMENT:
        raise CodegraphResumeConfigurationError("受管恢复配置快照不完整")
    if _normalize_config_path(Path(values["CODEV_PLATFORM_CONFIG"])) != expected_config:
        raise CodegraphResumeConfigurationError("受管服务配置覆盖与调用方不一致")
    if (
        _normalize_absolute_path(Path(values["PLATFORM_DATA_DIR"]), "受管服务数据根")
        != expected_data_root
    ):
        raise CodegraphResumeConfigurationError("受管服务数据根与调用方不一致")
    if not hmac.compare_digest(
        _normalize_digest(values["CODEV_REINDEX_CONFIG_SHA256"]), expected_digest
    ):
        raise CodegraphResumeConfigurationError("受管服务配置摘要与调用方不一致")


def _require_auxiliary_environment_files_safe(
    files: tuple[Path, ...],
    *,
    managed_path: Path,
    expected_config: Path,
    read_file: EnvironmentFileReader,
) -> None:
    for path in files:
        if path == managed_path:
            continue
        content = _read_environment_file(path, read_file)
        names = _parse_auxiliary_environment_file_names(content)
        protected = PROTECTED_ENVIRONMENT.intersection(names)
        if protected - {_CONFIG_OVERLAY_ENV} or FORBIDDEN_ENVIRONMENT in names:
            raise CodegraphResumeConfigurationError("受管服务环境文件覆盖恢复配置")
        if _CONFIG_OVERLAY_ENV in protected:
            _require_matching_auxiliary_config_overlay(content, expected_config)


def _require_matching_auxiliary_config_overlay(raw: bytes, expected_config: Path) -> None:
    """仅允许日常 root 环境文件重复声明同一固定配置路径。"""
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeError:
        raise CodegraphResumeConfigurationError("受管服务环境文件语法不受信任") from None
    assignments = [
        line.strip()
        for line in text.splitlines()
        if _is_config_overlay_assignment(line)
    ]
    expected = f"{_CONFIG_OVERLAY_ENV}={expected_config.as_posix()}"
    if assignments != [expected] or not hmac.compare_digest(
        assignments[0].encode("utf-8"),
        expected.encode("utf-8"),
    ):
        raise CodegraphResumeConfigurationError("受管服务配置覆盖恢复配置")


def _is_config_overlay_assignment(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith(("#", ";")):
        return False
    name, separator, _value = stripped.partition("=")
    return separator == "=" and name.strip() == _CONFIG_OVERLAY_ENV


def _read_environment_file(path: Path, read_file: EnvironmentFileReader) -> bytes:
    try:
        raw = read_file(path)
        if type(raw) is not bytes or len(raw) > _MAX_ENVIRONMENT_FILE_BYTES:
            raise CodegraphResumeConfigurationError("受管服务环境文件不受信任")
        return raw
    except CodegraphResumeConfigurationError:
        raise
    except Exception:
        raise CodegraphResumeConfigurationError("受管服务环境文件不受信任") from None


def _parse_auxiliary_environment_file_names(raw: bytes) -> frozenset[str]:
    """仅校验可信辅助文件的变量名，避免读取或约束令牌值语法。"""
    try:
        return parse_systemd_environment_keys(raw)
    except SystemdEnvironmentFileError:
        raise CodegraphResumeConfigurationError("受管服务环境文件语法不受信任") from None


def _parse_environment_file_content(raw: bytes) -> dict[str, str]:
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeError:
        raise CodegraphResumeConfigurationError("受管服务环境文件语法不受信任") from None
    if (
        "\\" in text
        or "'" in text
        or '"' in text
        or "\r" in text
        or any(ord(char) < 32 and char != "\n" for char in text)
    ):
        raise CodegraphResumeConfigurationError("受管服务环境文件语法不受信任")
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line:
            continue
        name, separator, value = line.partition("=")
        if (
            not separator
            or not value
            or _ENVIRONMENT_NAME.fullmatch(name) is None
            or name in values
            or any(char.isspace() for char in value)
        ):
            raise CodegraphResumeConfigurationError("受管服务环境文件语法不受信任")
        values[name] = value
    return values


def _require_environment_name_source_safe(raw: str) -> None:
    _require_plain_systemd_value(raw)
    for name in raw.strip().split():
        if _ENVIRONMENT_NAME.fullmatch(name) is None:
            raise CodegraphResumeConfigurationError("受管服务配置环境格式无效")
        if name in PROTECTED_ENVIRONMENT or name == FORBIDDEN_ENVIRONMENT:
            raise CodegraphResumeConfigurationError("受管服务环境来源覆盖恢复配置")


def _require_pam_name_empty(raw: str) -> None:
    _require_plain_systemd_value(raw)
    if raw.strip():
        raise CodegraphResumeConfigurationError("受管服务 PAM 环境无法证明")


def _require_plain_systemd_value(raw: str) -> None:
    if (
        type(raw) is not str
        or len(raw.encode("utf-8", "surrogatepass")) > _MAX_ENVIRONMENT_BYTES
        or "\x00" in raw
        or "\r" in raw
        or "\n" in raw.rstrip("\n")
        or "\\" in raw
        or "'" in raw
        or '"' in raw
    ):
        raise CodegraphResumeConfigurationError("受管服务配置环境格式无效")


def _default_command_runner(command: tuple[str, ...], *, timeout_sec: float) -> object:
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_sec,
        check=False,
    )


def _default_environment_file_reader(path: Path) -> bytes:
    from codev_platform.ops.reindex_codegraph_resume_environment_file import (
        read_trusted_environment_file,
    )

    return read_trusted_environment_file(path)


__all__ = [
    "CodegraphResumeConfigurationError",
    "verify_codegraph_resume_configuration",
]
