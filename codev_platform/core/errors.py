"""统一错误模型 (Workstream B) —— 机器可读 code + 稳定 http_status, 对外不泄漏 str(e)。

5 类对齐 2026-06-01 审计建议: 配置/入参错误 · 依赖缺失 · 索引缺失 · 权限错误 · 下游不可用
(+ 项目未知 / 限流 / 兜底 internal)。

设计约束:
- **叶子模块**: 只依赖 stdlib (enum), 不 import starlette / fastapi —— HTTP 边界自行把
  `to_http_payload()` 的 (body, status) 包成 JSONResponse / HTTPException, core 保持可被任意层复用。
- **向后兼容**: 对外错误体保留 `error` 字符串字段 (现有 MCP 客户端 / 测试断言子串),
  仅**并排新增** `code` 机器可读字段, 不改 `error` 文案、不改成嵌套对象。
- **不泄漏内部**: `PlatformError.message` 是对外稳定文案; `detail` (内部异常原文) 只进日志。
"""
from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    INVALID_PARAMS = "invalid_params"          # 400  入参非法 / 缺失
    ACCESS_DENIED = "access_denied"            # 403  ACL / org 不匹配
    PROJECT_UNKNOWN = "project_unknown"        # 404  project_id 未登记 / 未初始化
    DEPENDENCY_MISSING = "dependency_missing"  # 503  torch / 模型 / psycopg 等缺失
    INDEX_MISSING = "index_missing"            # 503  chroma / cross_layer DB 不存在
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"  # 503  下游 daemon / PG / LLM 不可用
    RATE_LIMITED = "rate_limited"              # 429
    INTERNAL = "internal"                      # 500  兜底 (不泄漏 str(e))


# code ↔ http_status 单一真值源。
_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_PARAMS: 400,
    ErrorCode.ACCESS_DENIED: 403,
    ErrorCode.PROJECT_UNKNOWN: 404,
    ErrorCode.DEPENDENCY_MISSING: 503,
    ErrorCode.INDEX_MISSING: 503,
    ErrorCode.UPSTREAM_UNAVAILABLE: 503,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.INTERNAL: 500,
}


def http_status(code: ErrorCode) -> int:
    """code → HTTP 状态码 (未知 code 兜底 500)。"""
    return _HTTP_STATUS.get(code, 500)


class PlatformError(Exception):
    """带 code 的对外错误。message 对外稳定文案; detail 仅进日志, 不进对外体。"""

    def __init__(self, code: ErrorCode, message: str, detail: str | None = None) -> None:
        self.code = code
        self.message = message
        self.detail = detail
        super().__init__(message)

    @property
    def http_status(self) -> int:
        return http_status(self.code)


def to_mcp_error(message_or_err, code: ErrorCode = ErrorCode.INTERNAL) -> dict:
    """MCP tool 错误体: {"error": <msg 字符串>, "code": <machine>}。

    向后兼容: `error` 仍是字符串 (现有客户端/测试读子串)。传 PlatformError 用其 message+code;
    传裸字符串则配 code 参数 (默认 internal)。
    """
    if isinstance(message_or_err, PlatformError):
        return {"error": message_or_err.message, "code": message_or_err.code.value}
    return {"error": str(message_or_err), "code": code.value}


def to_http_payload(err: PlatformError) -> tuple[dict, int]:
    """HTTP 边界用: 返回 (body, status_code)。body 同 MCP 体 (error 字符串 + code)。

    调用方自行 `JSONResponse(body, status_code=status)` / `HTTPException(status, detail=body)`,
    使本模块不依赖具体 web 框架。
    """
    return {"error": err.message, "code": err.code.value}, err.http_status
