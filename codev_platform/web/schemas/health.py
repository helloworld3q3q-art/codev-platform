"""Health 响应模型 (plan §十五 Health)。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class HealthData(BaseModel):
    status: str = "ok"
    service: str = "codev-platform-web"
    # 可选下游依赖探测结果 (plan §二十二 降级矩阵): dep -> "ok"|"degraded"|"down"
    dependencies: dict[str, str] = Field(default_factory=dict)
