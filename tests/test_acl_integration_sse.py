"""ACL 入口级集成测试 — MCP SSE 入口 (audit #3)。

纯函数测试 (test_acl.py) 只覆盖 can_access 本身, 易"测试绿但入口漏接"。本测真起一个
挂了 AuthMiddleware 的最小 Starlette app, 用 TestClient 打 GET /sse?project_id=, 复刻
3 个 MCP server run_http 的 handle_sse 结构 (认证中间件 → request.state.identity →
can_access → 403/放行), 验证 ACL 真在请求路径上生效。

不进真实 connect_sse (长连接会 hang): handle_sse 命中 ACL 后, allow 分支返回 200 占位,
deny 分支返回 403 JSONResponse — 与 server.py 中 deny 同一代码路径。

⚠️ mock handle_sse 必须与 chroma/graph/codegraph server.py 的 handle_sse **回退顺序一致**:
无 ?project_id= 时先置 pid=None 过 ACL(token 模式 can_access(None)=deny), ACL 放行后才回退
默认 _DEFAULT_PID。绝不能"先回退默认再 ACL"(那会让 token 省略 project_id 静默命中默认项目 =
越权扫盲, audit 2026-06-01 #1 抓到的原始缺口)。test_sse_token_no_project_id_* 钉死此顺序。
"""
from __future__ import annotations

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from codev_platform.core.acl import can_access
from codev_platform.gateway import AuthMiddleware
from codev_platform.gateway.auth import PassthroughAuthenticator, TokenAuthenticator, token_hash

# 模拟真 server 的 PROJECT_ID(daemon 启动默认项目, 恒非 None)。
_DEFAULT_PID = "platform-default"


def _build_app(authenticator, cfg):
    """复刻 MCP server run_http 的 handle_sse 结构(含回退顺序): Route(/sse) + AuthMiddleware。"""

    async def handle_sse(request):
        # 复刻 server.py: 无 ?project_id= → pid=None(*不*提前回退默认), 先过 ACL
        pid_raw = request.query_params.get("project_id")
        pid = pid_raw if pid_raw else None
        ident = getattr(request.state, "identity", None)
        dec = can_access(cfg, ident, pid)
        if not dec.allowed:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        # ACL 放行后才回退默认(仅 passthrough 会到这; token 无显式 project 已被拒)
        if pid is None:
            pid = _DEFAULT_PID
        # allow 分支: 真 server 会进 connect_sse 长连接; 测试只断言"放行到此 + 路由到的 pid"
        return JSONResponse({"ok": True, "advisory": dec.advisory, "routed_pid": pid})

    return Starlette(
        routes=[Route("/sse", handle_sse, methods=["GET"])],
        middleware=[Middleware(AuthMiddleware, authenticator=authenticator, public_paths={"/health"})],
    )


_TOK = "secret-token-xyz"
_TOK_CFG = {"gateway": {"auth_mode": "token"}, "projects": {}}


def _token_auth(projects):
    return TokenAuthenticator({token_hash(_TOK): {"user_id": "u1", "org_id": "orgA", "projects": projects}})


# ---- passthrough(dev): 任意 project 放行, 不被 ACL 403 ----

def test_sse_passthrough_allows_any_project():
    app = _build_app(PassthroughAuthenticator(), cfg={"gateway": {"auth_mode": "passthrough"}})
    r = TestClient(app).get("/sse", params={"project_id": "openclaw-stock"})
    assert r.status_code == 200
    assert r.json()["advisory"] is True  # passthrough = advisory 放行


def test_sse_passthrough_no_project_id_falls_back_to_default():
    # passthrough 无 ?project_id= → ACL 放行(advisory)→ 回退默认 PROJECT_ID(向后兼容不破)
    app = _build_app(PassthroughAuthenticator(), cfg={"gateway": {"auth_mode": "passthrough"}})
    r = TestClient(app).get("/sse")
    assert r.status_code == 200
    assert r.json()["routed_pid"] == _DEFAULT_PID  # 回退在 ACL 放行之后


# ---- token(prod): 三道闸 ----

def test_sse_token_missing_bearer_is_401():
    # 无 Authorization 头 → 中间件认证失败, 根本到不了 handle_sse
    app = _build_app(_token_auth(["openclaw-stock"]), _TOK_CFG)
    r = TestClient(app).get("/sse", params={"project_id": "openclaw-stock"})
    assert r.status_code == 401


def test_sse_token_project_not_in_allowlist_is_403():
    app = _build_app(_token_auth(["other-proj"]), _TOK_CFG)
    r = TestClient(app).get(
        "/sse", params={"project_id": "openclaw-stock"},
        headers={"Authorization": f"Bearer {_TOK}"},
    )
    assert r.status_code == 403


def test_sse_token_project_in_allowlist_allowed():
    app = _build_app(_token_auth(["openclaw-stock"]), _TOK_CFG)
    r = TestClient(app).get(
        "/sse", params={"project_id": "openclaw-stock"},
        headers={"Authorization": f"Bearer {_TOK}"},
    )
    assert r.status_code == 200
    assert r.json()["advisory"] is False  # token = 真授权, 非 advisory


def test_sse_token_no_project_id_is_403():
    # token 模式无 ?project_id= → pid=None → can_access deny(防越权扫盲), 不静默回退默认
    app = _build_app(_token_auth(["openclaw-stock"]), _TOK_CFG)
    r = TestClient(app).get("/sse", headers={"Authorization": f"Bearer {_TOK}"})
    assert r.status_code == 403


def test_sse_token_no_project_id_denied_even_if_default_in_allowlist():
    """最强回归(钉死回退顺序): token 白名单含默认项目, 省略 ?project_id= 仍须 403。

    若哪天有人把"回退默认 PROJECT_ID"挪回 ACL *之前*(audit #1 原始缺口), pid 会变成
    白名单内的 _DEFAULT_PID → can_access 放行 → 200, 本用例即翻红。token 必须显式带 project。"""
    app = _build_app(_token_auth([_DEFAULT_PID]), _TOK_CFG)  # 默认项目在白名单内
    r = TestClient(app).get("/sse", headers={"Authorization": f"Bearer {_TOK}"})
    assert r.status_code == 403  # 仍拒: token 模式 None 在回退前已被 can_access 挡下


def test_sse_token_all_projects_still_needs_explicit_project_id():
    # 连 all_projects=* 的 token 也必须显式带 project_id(None 在闸2前就被拒)
    app = _build_app(_token_auth("*"), _TOK_CFG)
    r = TestClient(app).get("/sse", headers={"Authorization": f"Bearer {_TOK}"})
    assert r.status_code == 403
