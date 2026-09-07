"""项目登记表路径解析的单一入口。

项目元数据随应用 wheel 以只读资源发布；运行时如需使用受控的外部登记表，
可通过 ``CODEV_PLATFORM_META`` 显式覆盖。历史 ``PLATFORM_META_DIR`` 仅为
Agent 调用方兼容保留，优先级低于新变量。
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


PLATFORM_META_ENV_VAR = "CODEV_PLATFORM_META"
_LEGACY_PLATFORM_META_ENV_VAR = "PLATFORM_META_DIR"


def platform_meta_projects_dir(
    *,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """返回项目登记表目录：显式覆盖优先，否则使用已安装资源包。"""
    selected_environment = os.environ if environment is None else environment
    configured = _configured_projects_dir(selected_environment)
    if configured is not None:
        return configured
    return _bundled_projects_dir()


def _configured_projects_dir(environment: Mapping[str, str]) -> Path | None:
    for name in (PLATFORM_META_ENV_VAR, _LEGACY_PLATFORM_META_ENV_VAR):
        value = environment.get(name)
        if value:
            return Path(value).expanduser()
    return None


def _bundled_projects_dir() -> Path:
    """从安装后的资源包解析路径；源码树回退仅兼容旧开发环境。"""
    try:
        import platform_meta

        package_file = getattr(platform_meta, "__file__", None)
        if package_file:
            return Path(package_file).resolve().parent / "projects"
    except ModuleNotFoundError as exc:
        if exc.name != "platform_meta":
            raise
    return Path(__file__).resolve().parents[2] / "platform_meta" / "projects"


__all__ = ["PLATFORM_META_ENV_VAR", "platform_meta_projects_dir"]
