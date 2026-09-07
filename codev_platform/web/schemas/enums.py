"""Enum 元数据请求模型 (plan §二十一)。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class EnumListRequest(BaseModel):
    enumType: str | None = Field(None, description="枚举类型; 不传返回全部")
