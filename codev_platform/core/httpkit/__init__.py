"""core.httpkit —— agent 与 web 共用的 HTTP 骨架 (plan D7)。

叶子层: 只依赖 core.* + gateway + fastapi, 不 import 任何业务模块 (web/agent 路由/service)。
装"任意 HTTP 入口共用"的部分: envelope / 分页 / 权限依赖 / app 工厂 / openapi / 枚举机制。
"""
from codev_platform.core.httpkit.app_factory import build_app
from codev_platform.core.httpkit.enums import BaseEnum, EnumItem, EnumRegistry
from codev_platform.core.httpkit.envelope import (
    CommonResult,
    PageResult,
    error_response,
    internal_error_response,
    ok,
    page,
)
from codev_platform.core.httpkit.openapi import duplicate_operation_ids
from codev_platform.core.httpkit.pagination import PageParams, page_params
from codev_platform.core.httpkit.permissions import require_project_access
from codev_platform.core.httpkit.request_id import RequestIdMiddleware

__all__ = [
    "build_app",
    "BaseEnum",
    "EnumItem",
    "EnumRegistry",
    "CommonResult",
    "PageResult",
    "ok",
    "page",
    "error_response",
    "internal_error_response",
    "duplicate_operation_ids",
    "PageParams",
    "page_params",
    "require_project_access",
    "RequestIdMiddleware",
]
