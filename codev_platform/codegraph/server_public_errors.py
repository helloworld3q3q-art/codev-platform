"""CodeGraph 代理对外错误与内部详情脱敏策略。"""

from __future__ import annotations

from codev_platform.core.obslog import redact_text

PUBLIC_BACKEND_FAILURE = "CodeGraph 后端执行失败"
PUBLIC_TOOL_FAILURE = "CodeGraph 工具执行失败"
BACKEND_CALL_FAILED = "backend_call_failed"
BACKEND_RETURNED_ERROR = "backend_returned_error"


def protected_error_detail(error: object) -> str:
    """内部日志只保留长度与摘要，不输出异常原文、路径或凭据。"""
    return str(redact_text(str(error), "prod"))


__all__ = [
    "BACKEND_CALL_FAILED",
    "BACKEND_RETURNED_ERROR",
    "PUBLIC_BACKEND_FAILURE",
    "PUBLIC_TOOL_FAILURE",
    "protected_error_detail",
]
