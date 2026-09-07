"""运行时依赖 wheel 与当前 Python ABI 的兼容性证明。"""

from __future__ import annotations

from collections.abc import Iterable

try:
    from packaging.tags import Tag, sys_tags
    from packaging.utils import canonicalize_name, parse_wheel_filename
    from packaging.version import InvalidVersion, Version
except ModuleNotFoundError:  # 干净 venv 自举期只保证存在 pip 自带的同版解析器。
    from pip._vendor.packaging.tags import Tag, sys_tags
    from pip._vendor.packaging.utils import canonicalize_name, parse_wheel_filename
    from pip._vendor.packaging.version import InvalidVersion, Version


class RuntimeWheelCompatibilityError(ValueError):
    """wheel 文件名、发行包身份或平台标签不受当前解释器支持。"""


def require_compatible_wheel(
    filename: str,
    *,
    package: str,
    version: str,
    supported_tags: Iterable[Tag] | None = None,
) -> None:
    """证明一个 wheel 精确匹配 pin，并至少命中一个当前解释器标签。"""
    try:
        parsed_name, parsed_version, _build, wheel_tags = parse_wheel_filename(filename)
        expected_version = Version(version)
    except (InvalidVersion, TypeError, ValueError):
        raise RuntimeWheelCompatibilityError("wheel 文件名或版本无效") from None
    if canonicalize_name(parsed_name) != package or parsed_version != expected_version:
        raise RuntimeWheelCompatibilityError("wheel 与精确 pin 不一致")
    accepted = frozenset(sys_tags() if supported_tags is None else supported_tags)
    if not accepted or wheel_tags.isdisjoint(accepted):
        raise RuntimeWheelCompatibilityError("wheel 与当前 Python ABI 不兼容")


__all__ = ["RuntimeWheelCompatibilityError", "require_compatible_wheel"]
