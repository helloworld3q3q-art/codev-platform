"""Web backend 契约测试 (Track B / Phase 8) —— operationId 唯一 + OpenAPI 可生成。

生成前端 typings 依赖 OpenAPI 的 operationId 唯一(重复会让 `pnpm run api` 生成错乱/覆盖)。
本测试在装配完整 web app 后断言:全 route operationId 无重复 + OpenAPI 能生成 + 关键路由在册。
新增路由若撞 operationId, 这里当场红。
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from codev_platform.core.httpkit import duplicate_operation_ids  # noqa: E402
from codev_platform.web.app import create_app  # noqa: E402
from tests.runtime_identity_support import fake_runtime_identity  # noqa: E402

_CFG = {"gateway": {"auth_mode": "passthrough"}, "projects": {}}


def _app():
    return create_app(_CFG, identity_loader=fake_runtime_identity)


def test_operation_ids_unique():
    # 复用 httpkit 共享去重助手 (test_web_graph 同款), 不在测试里重造一遍扫描逻辑。
    app = _app()
    dupes = duplicate_operation_ids(app)
    assert dupes == [], f"重复 operationId (前端 pnpm run api 会生成错乱): {dupes}"


def test_openapi_generates_and_has_key_routes():
    app = _app()
    spec = app.openapi()  # 不抛即视为 schema 可生成
    assert spec.get("openapi") and spec.get("paths")
    op_ids = {
        op["operationId"]
        for path in spec["paths"].values()
        for op in path.values()
        if isinstance(op, dict) and "operationId" in op
    }
    # 关键路由快照: 在册即可 (新增/改名路由更新此集合, 防意外删除核心接口)
    for must in ("listProjects", "authLogin", "reportImpact", "graphUnifiedGraph", "rebuildIndex"):
        assert must in op_ids, f"关键 operationId 缺失: {must}"


def test_openapi_paths_all_have_operation_id():
    """每个 POST/GET 操作都应有显式 operationId (平台 §十三 约束, 保证生成名稳定)。"""
    app = _app()
    spec = app.openapi()
    missing = []
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            if isinstance(op, dict) and "operationId" not in op:
                missing.append(f"{method.upper()} {path}")
    assert not missing, f"以下操作缺显式 operationId: {missing}"
