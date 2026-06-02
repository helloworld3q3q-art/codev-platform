"""统一响应 envelope —— 对齐 stock-admin-web 前端 BaseApiResponse (plan §六 / D3 修订)。

形状与业务前端 utils/fetch 的 BaseApiResponse 完全一致, 使新 admin 前端可逐字复用
fetch.ts / types.ts / enum.ts / ProTable 全套, 零改请求层:
- result: 业务码, 0=成功 / 非0=失败 (与 HTTP status 正交; 前端 responseCodeHandler 判 result===0);
- message: 对外文案;
- data: 业务数据;
- errors: [{errorCode, errorMessage, field?}] (errorCode 复用 core.errors.ErrorCode 8 类或 sub-code);
- 分页: currentPage / pageSize / total / totalPage;
- requestId: 透传 RequestIdMiddleware (额外字段, 前端忽略不影响兼容)。

> 注: 这里 result=0 是**业务码**(0=成功), 与 HTTP status 正交, 是成熟约定 —— 不是 D3 反对的
> result:200(双状态码)。错误响应仍带正确 HTTP 4xx/5xx, 前端 401/403 拦截器照常工作。

适配复用 core.errors.to_http_payload 拿真值 (HTTP status 单一真值源); core.errors 不 import fastapi。
"""
from __future__ import annotations

from typing import Generic, TypeVar

from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from codev_platform.core.errors import ErrorCode, PlatformError, to_http_payload

T = TypeVar("T")


class ErrorItem(BaseModel):
    """错误项 (对齐前端 BaseApiResponse.errors[])。"""

    errorCode: str
    errorMessage: str
    field: str | None = None


class CommonResult(BaseModel, Generic[T]):
    """单对象统一响应。result=0 成功 + data; 非0 失败 + errors。"""

    result: int = 0
    message: str = "OK"
    data: T | None = None
    errors: list[ErrorItem] = Field(default_factory=list)
    requestId: str | None = None


class PageResult(BaseModel, Generic[T]):
    """分页统一响应。字段对齐前端 (currentPage/pageSize/total/totalPage)。"""

    result: int = 0
    message: str = "OK"
    data: list[T] = Field(default_factory=list)
    currentPage: int = 1
    pageSize: int = 20
    total: int = 0
    totalPage: int = 0
    errors: list[ErrorItem] = Field(default_factory=list)
    requestId: str | None = None


def ok(data: T | None = None, *, request_id: str | None = None) -> CommonResult[T]:
    """成功体 helper (result=0)。"""
    return CommonResult(result=0, message="OK", data=data, requestId=request_id)


def page(
    data: list[T],
    *,
    page_number: int = 1,
    page_size: int = 20,
    total: int = 0,
    request_id: str | None = None,
) -> PageResult[T]:
    """分页体 helper。totalPage 由 total/pageSize 算 (前端不再自算)。"""
    total_page = (total + page_size - 1) // page_size if page_size > 0 else 0
    return PageResult(
        result=0, message="OK", data=data,
        currentPage=page_number, pageSize=page_size, total=total, totalPage=total_page,
        requestId=request_id,
    )


def error_response(
    err: PlatformError,
    *,
    request_id: str | None = None,
    error_code: str | None = None,
) -> JSONResponse:
    """PlatformError → envelope JSONResponse。HTTP status 复用 to_http_payload (单一真值源)。

    result=1 + errors[{errorCode, errorMessage}]; errorCode 默认填 8 类机器码 (body["code"]),
    传 error_code 则用 sub-code (plan §十 细分)。detail 只进日志, 不进体。
    """
    body, status = to_http_payload(err)
    item = ErrorItem(errorCode=error_code or body["code"], errorMessage=body["error"])
    payload = CommonResult(
        result=1, message=body["error"], data=None,
        errors=[item], requestId=request_id,
    ).model_dump()
    return JSONResponse(payload, status_code=status)


def internal_error_response(request_id: str | None = None) -> JSONResponse:
    """非 PlatformError 异常的兜底 (不泄漏 str(e))。"""
    return error_response(
        PlatformError(ErrorCode.INTERNAL, "internal error"),
        request_id=request_id,
    )
