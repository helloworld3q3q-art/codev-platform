"""Enum 元数据路由 (plan §二十一) —— 统一返回业务枚举给前端下拉。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from codev_platform.core.errors import ErrorCode, PlatformError
from codev_platform.core.httpkit.enums import EnumItem
from codev_platform.core.httpkit.envelope import CommonResult, ok
from codev_platform.web.domain.enums import registry
from codev_platform.web.schemas.enums import EnumListRequest

router = APIRouter()


@router.post(
    "/api/v1/enums/list",
    tags=["EnumAPI-枚举元数据"],
    summary="枚举元数据-查询枚举列表",
    operation_id="listEnums",
    response_model=CommonResult[dict[str, list[EnumItem]]],
)
def list_enums(request: Request, body: EnumListRequest | None = None) -> CommonResult[dict[str, list[EnumItem]]]:
    enum_type = body.enumType if body else None
    try:
        data = registry.list(enum_type)
    except KeyError as exc:
        raise PlatformError(ErrorCode.INVALID_PARAMS, f"unknown enumType: {enum_type}",
                            detail=repr(exc)) from exc
    rid = getattr(request.state, "request_id", None)
    return ok(data, request_id=rid)
