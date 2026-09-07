"""core/errors.py 统一错误模型 (Workstream B) 单测。

覆盖: code↔http_status 映射完整 + PlatformError + to_mcp_error/to_http_payload shape
(含向后兼容: error 仍是字符串子串可读)。
"""
from codev_platform.core.errors import (
    ErrorCode,
    PlatformError,
    http_status,
    to_mcp_error,
    to_http_payload,
)


def test_every_code_has_http_status():
    # 每个 ErrorCode 都必须有映射 (否则 http_status 兜底 500 掩盖漏配)
    for code in ErrorCode:
        st = http_status(code)
        assert isinstance(st, int) and 400 <= st < 600


def test_http_status_values():
    assert http_status(ErrorCode.INVALID_PARAMS) == 400
    assert http_status(ErrorCode.ACCESS_DENIED) == 403
    assert http_status(ErrorCode.PROJECT_UNKNOWN) == 404
    assert http_status(ErrorCode.RATE_LIMITED) == 429
    assert http_status(ErrorCode.DEPENDENCY_MISSING) == 503
    assert http_status(ErrorCode.INDEX_MISSING) == 503
    assert http_status(ErrorCode.UPSTREAM_UNAVAILABLE) == 503
    assert http_status(ErrorCode.INTERNAL) == 500


def test_platform_error_fields():
    e = PlatformError(ErrorCode.INDEX_MISSING, "chroma collection 不存在", detail="NotFoundError: x")
    assert e.code is ErrorCode.INDEX_MISSING
    assert e.message == "chroma collection 不存在"
    assert e.detail == "NotFoundError: x"
    assert e.http_status == 503
    # detail 不应进对外体
    body = to_mcp_error(e)
    assert "NotFoundError" not in body["error"]


def test_to_mcp_error_from_string_backward_compat():
    # 裸字符串 → error 字段保留原文 (向后兼容子串断言), code 默认 internal
    body = to_mcp_error("query 不能为空", ErrorCode.INVALID_PARAMS)
    assert body["error"] == "query 不能为空"
    assert body["code"] == "invalid_params"
    # 默认 code
    assert to_mcp_error("boom")["code"] == "internal"


def test_to_mcp_error_from_platform_error():
    e = PlatformError(ErrorCode.ACCESS_DENIED, "forbidden")
    body = to_mcp_error(e)
    assert body == {"error": "forbidden", "code": "access_denied"}


def test_to_http_payload():
    e = PlatformError(ErrorCode.PROJECT_UNKNOWN, "project foo 未初始化")
    body, status = to_http_payload(e)
    assert status == 404
    assert body["error"] == "project foo 未初始化"
    assert body["code"] == "project_unknown"


def test_code_value_is_str():
    # ErrorCode 是 str enum, .value 直接可序列化进 JSON
    assert ErrorCode.INTERNAL.value == "internal"
    assert ErrorCode.INVALID_PARAMS == "invalid_params"  # str enum 等值比较
