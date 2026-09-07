"""枚举元数据机制 —— 复刻 stock-admin-api EnumMetadataService 契约 (plan §二十一)。

机制 (协议 + 注册表 + 项 DTO) 放这里, 业务枚举定义放各服务 domain/enums.py。
前端 useModel('enum') / pnpm run enums 无感复用; 字段名保持 camelCase 与 Java EnumItemDTO 对齐。

禁止前端硬编码业务枚举 (对齐 cross-layer-enum-consistency 规则): 真值源在后端枚举, 前端只取 options。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


@runtime_checkable
class BaseEnum(Protocol):
    """业务枚举项契约。每个成员暴露 6 个元数据字段 (与 Java EnumSpecification 对齐)。"""

    @property
    def enum_type(self) -> str: ...        # 如 "ProjectStatusEnum"

    @property
    def enum_value(self) -> str: ...       # 如 "ACTIVE"

    @property
    def local_language(self) -> str: ...   # 中文展示

    @property
    def enum_order(self) -> int: ...

    @property
    def display_name(self) -> str: ...

    @property
    def description(self) -> str: ...


class EnumItem(BaseModel):
    """枚举项响应 DTO。字段名 camelCase 对齐前端 (Java EnumItemDTO 同构)。"""

    enumType: str
    enumValue: str
    localLanguage: str
    enumOrder: int
    displayName: str
    description: str = ""


class EnumRegistry:
    """枚举注册表。register 业务枚举类 → list 输出前端下拉所需的 {enumType: [EnumItem]}。

    register 的类需可迭代成员, 每成员实现 BaseEnum 协议 (含 enum_type/enum_value/.. 6 字段)。
    list 结果缓存 (枚举是静态元数据, 进程内不变); 注册后失效缓存。
    """

    def __init__(self) -> None:
        self._by_type: dict[str, list[EnumItem]] = {}

    def register(self, enum_cls) -> None:
        """注册一个枚举类。成员需实现 BaseEnum (枚举值定义在 domain/enums.py)。"""
        members = list(enum_cls)
        if not members:
            return
        items = [
            EnumItem(
                enumType=m.enum_type,
                enumValue=m.enum_value,
                localLanguage=m.local_language,
                enumOrder=m.enum_order,
                displayName=m.display_name,
                description=m.description,
            )
            for m in members
        ]
        items.sort(key=lambda it: it.enumOrder)
        self._by_type[members[0].enum_type] = items

    def list(self, enum_type: str | None = None) -> dict[str, list[EnumItem]]:
        """enum_type=None → 全部; 否则单类。未知 enum_type 抛 KeyError (路由转 INVALID_PARAMS)。"""
        if not enum_type:
            return dict(self._by_type)
        if enum_type not in self._by_type:
            raise KeyError(enum_type)
        return {enum_type: self._by_type[enum_type]}
