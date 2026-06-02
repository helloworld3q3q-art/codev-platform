"""统一响应 envelope —— Web Backend / agent 共用 (plan §六 / D3 / D7)。

扁平体: success / data / code / error / errorCode / requestId。
- code  = core.errors.ErrorCode.value (8 类), 决定 HTTP status + 程序分支锚点;
- error = 对外 message (与 MCP/HTTP {error,code} 字面量同构, 向后兼容);
- errorCode = 可选 sub-code (plan §十 细分字符串), 纯展示不影响 status;
- 去掉初稿冗余数字 result (双状态码) 与嵌套 errors[] (过度设计)。

适配复用 core.errors.to_http_payload 拿真值, core.errors 不 import fastapi (叶子)。
本模块是 web 边界, 允许 import fastapi。
"""
from __future__ import annotations

from typing import Generic, TypeVar

from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from codev_platform.core.errors import ErrorCode, PlatformError, to_http_payload

T = TypeVar("T")


class CommonResult(BaseModel, Generic[T]):
    """单对象统一响应。成功 success=True + data;失败 success=False + code/error。"""

    success: bool = True
    data: T | None = None
    code: str | None = None        # 失败=ErrorCode.value;成功=None
    error: str | None = None       # 失败=对外 message;成功=None
    errorCode: str | None = None   # 可选 sub-code (§十), 纯展示
    requestId: str | None = None


class PageResult(BaseModel, Generic[T]):
    """分页统一响应。字段对齐 plan §七 (pageNumber/pageSize/total/nextToken)。"""

    success: bool = True
    data: list[T] = Field(default_factory=list)
    pageNumber: int = 1
    pageSize: int = 20
    total: int = 0
    nextToken: str | None = None
    code: str | None = None
    error: str | None = None
    errorCode: str | None = None
    requestId: str | None = None


def ok(data: T | None = None, *, request_id: str | None = None) -> CommonResult[T]:
    """成功体 helper。"""
    return CommonResult(success=True, data=data, requestId=request_id)


def page(
    data: list[T],
    *,
    page_number: int = 1,
    page_size: int = 20,
    total: int = 0,
    next_token: str | None = None,
    request_id: str | None = None,
) -> PageResult[T]:
    """分页体 helper。"""
    return PageResult(
        success=True, data=data, pageNumber=page_number, pageSize=page_size,
        total=total, nextToken=next_token, requestId=request_id,
    )


def error_response(
    err: PlatformError,
    *,
    request_id: str | None = None,
    error_code: str | None = None,
) -> JSONResponse:
    """PlatformError → envelope JSONResponse。HTTP status 复用 to_http_payload (单一真值源)。

    body["error"]/body["code"] 即对外 message + 8 类机器码;detail 只进日志, 不进体。
    """
    body, status = to_http_payload(err)
    payload = CommonResult(
        success=False, data=None,
        code=body["code"], error=body["error"],
        errorCode=error_code, requestId=request_id,
    ).model_dump()
    return JSONResponse(payload, status_code=status)


def internal_error_response(request_id: str | None = None) -> JSONResponse:
    """非 PlatformError 异常的兜底 (不泄漏 str(e))。"""
    return error_response(
        PlatformError(ErrorCode.INTERNAL, "internal error"),
        request_id=request_id,
    )
