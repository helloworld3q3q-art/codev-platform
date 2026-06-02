"""Web Backend 地基测试 (plan Phase 1 Gate) —— httpkit + web 骨架。

覆盖: health envelope + RequestId 头 + 枚举统一返回 + 未知枚举走统一错误 envelope +
operationId 无重复。本 venv 未装 fastapi → importorskip 自动 skip。
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from codev_platform.core.httpkit import duplicate_operation_ids  # noqa: E402
from codev_platform.web.app import create_app  # noqa: E402

# 强制 passthrough, 不依赖本机 ~/.codev-platform/config.json (可能是 token 模式)
_CFG = {"gateway": {"auth_mode": "passthrough"}, "projects": {}}


def _client() -> TestClient:
    return TestClient(create_app(cfg=_CFG))


def test_health_check_envelope_and_request_id():
    r = _client().get("/api/v1/health/check")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"]["status"] == "ok"
    assert body["code"] is None and body["error"] is None
    # RequestId 中间件: 响应头 + body.requestId 都在且一致
    assert r.headers.get("x-request-id")
    assert body["requestId"] == r.headers["x-request-id"]


def test_health_probe_plain():
    r = _client().get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_request_id_propagated_from_client():
    r = _client().get("/api/v1/health/check", headers={"X-Request-Id": "rid-123"})
    assert r.headers["x-request-id"] == "rid-123"
    assert r.json()["requestId"] == "rid-123"


def test_enums_list_all():
    r = _client().post("/api/v1/enums/list", json={})
    assert r.status_code == 200
    data = r.json()["data"]
    assert "ProjectStatusEnum" in data
    items = data["ProjectStatusEnum"]
    # camelCase 字段 + 按 order 排序 (与 Java EnumItemDTO 对齐)
    assert items[0]["enumValue"] == "ACTIVE"
    assert items[0]["localLanguage"] == "启用"
    assert [it["enumOrder"] for it in items] == sorted(it["enumOrder"] for it in items)


def test_enums_list_single_type():
    r = _client().post("/api/v1/enums/list", json={"enumType": "MemberRoleEnum"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert set(data.keys()) == {"MemberRoleEnum"}
    assert {it["enumValue"] for it in data["MemberRoleEnum"]} == {"viewer", "member", "admin"}


def test_enums_unknown_type_is_unified_error_envelope():
    r = _client().post("/api/v1/enums/list", json={"enumType": "NopeEnum"})
    assert r.status_code == 400
    body = r.json()
    assert body["success"] is False
    assert body["code"] == "invalid_params"
    assert body["requestId"]  # 错误体也带 request_id


def test_no_duplicate_operation_ids():
    assert duplicate_operation_ids(create_app(cfg=_CFG)) == []
