"""Web Backend 业务枚举真值源 (plan §二十一)。

每个枚举成员带 (value, 中文 label, order, desc) 元数据, 实现 core.httpkit.BaseEnum 协议,
注册进 module 级 registry → POST /api/v1/enums/list 统一输出 → 前端 useModel('enum')。
新增/改业务枚举改这里 (单一真值源), 前端重生成, 禁前端本地造 options (cross-layer-enum-consistency)。
"""
from __future__ import annotations

from enum import Enum

from codev_platform.core.httpkit.enums import EnumRegistry


class _MetaEnum(str, Enum):
    """带前端元数据的字符串枚举基类。成员定义 = (value, 中文label, order, desc?)。"""

    def __new__(cls, value: str, label: str, order: int, desc: str = ""):
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj._label = label
        obj._order = order
        obj._desc = desc
        return obj

    @property
    def enum_type(self) -> str:
        return type(self).__name__

    @property
    def enum_value(self) -> str:
        return str(self.value)

    @property
    def local_language(self) -> str:
        return self._label

    @property
    def enum_order(self) -> int:
        return self._order

    @property
    def display_name(self) -> str:
        return self._label

    @property
    def description(self) -> str:
        return self._desc


class OrgStatusEnum(_MetaEnum):
    ACTIVE = ("ACTIVE", "启用", 1, "组织正常使用")
    DISABLED = ("DISABLED", "禁用", 2, "组织禁用, 其下项目不可访问")


class UserStatusEnum(_MetaEnum):
    ACTIVE = ("ACTIVE", "启用", 1, "用户正常")
    DISABLED = ("DISABLED", "禁用", 2, "用户禁用, token/session 失效")


class ProjectStatusEnum(_MetaEnum):
    ACTIVE = ("ACTIVE", "启用", 1, "项目正常使用")
    ARCHIVED = ("ARCHIVED", "归档", 2, "项目已归档, 只读")


class MemberRoleEnum(_MetaEnum):
    VIEWER = ("viewer", "只读", 1, "只读访问")
    MEMBER = ("member", "成员", 2, "可写, 触发分析")
    ADMIN = ("admin", "管理员", 3, "管理成员/配置")


class JobStatusEnum(_MetaEnum):
    PENDING = ("Pending", "排队中", 1, "")
    RUNNING = ("Running", "运行中", 2, "")
    SUCCEEDED = ("Succeeded", "成功", 3, "")
    FAILED = ("Failed", "失败", 4, "")
    CANCELLED = ("Cancelled", "已取消", 5, "")


registry = EnumRegistry()
for _enum in (OrgStatusEnum, UserStatusEnum, ProjectStatusEnum, MemberRoleEnum, JobStatusEnum):
    registry.register(_enum)
