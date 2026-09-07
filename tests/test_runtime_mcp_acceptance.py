"""四套 MCP 的真实 Streamable HTTP 工具级验收。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import codev_platform.runtime_mcp_acceptance as mcp_acceptance
from codev_platform.runtime_deployment_contract import RuntimeDeploymentError
from codev_platform.runtime_mcp_acceptance import (
    MCPAcceptanceCall,
    MCPServiceEndpoint,
    MCPToolResponse,
    verify_mcp_acceptance_async,
)


_ENDPOINTS = (
    MCPServiceEndpoint("platform-docs", 18083),
    MCPServiceEndpoint("codegraph", 18091),
    MCPServiceEndpoint("agent-memory", 18087),
    MCPServiceEndpoint("graph", 18092),
)


def _successful_payload(tool: str) -> dict[str, object]:
    if tool == "list_collections":
        return {
            "project_id": "codev-platform",
            "collection": "docs",
            "total_chunks": 0,
            "by_category": {},
            "by_module": {},
        }
    if tool == "codegraph_status":
        return {
            "project_id": "codev-platform",
            "tool": "codegraph_status",
            "merge": "codev-fanout-v1",
            "repos": [
                {
                    "repo": "main",
                    "ok": True,
                    "is_error": False,
                    "content": [{"type": "text", "text": "ready"}],
                }
            ],
            "failures": [],
        }
    if tool == "search_nodes":
        return {
            "project_id": "codev-platform",
            "tool": "search_nodes",
            "query": "__runtime_acceptance__",
            "kind": "all",
            "hits": [],
            "count": 0,
        }
    if tool == "recall":
        return {
            "project_id": "codev-platform",
            "tool": "recall",
            "query": "__runtime_acceptance__",
            "entries": [],
            "count": 0,
        }
    raise AssertionError(f"测试未定义工具回执：{tool}")


def _config(*, auth_mode: str = "token") -> dict[str, object]:
    return {
        "gateway": {"auth_mode": auth_mode},
        "platform": {"token_env": "PLATFORM_TOKEN"},
    }


def test_按固定工具契约调用四个loopback端点且token不进入证据() -> None:
    calls: list[tuple[str, dict[str, str], str, dict[str, object]]] = []

    async def caller(
        url: str,
        headers: dict[str, str],
        tool: str,
        arguments: dict[str, object],
    ) -> MCPToolResponse:
        calls.append((url, headers, tool, arguments))
        return MCPToolResponse(
            is_error=False,
            payloads=(_successful_payload(tool),),
        )

    evidence = asyncio.run(
        verify_mcp_acceptance_async(
            _config(),
            "codev-platform",
            endpoints=_ENDPOINTS,
            environment={"PLATFORM_TOKEN": "never-in-receipt-token"},
            caller=caller,
        )
    )

    assert [(url, tool, arguments) for url, _headers, tool, arguments in calls] == [
        ("http://127.0.0.1:18083/mcp?project_id=codev-platform", "list_collections", {}),
        (
            "http://127.0.0.1:18091/mcp?project_id=codev-platform",
            "codegraph_status",
            {"_codev_merge": "structured"},
        ),
        (
            "http://127.0.0.1:18092/mcp?project_id=codev-platform",
            "search_nodes",
            {"query": "__runtime_acceptance__", "limit": 1},
        ),
        (
            "http://127.0.0.1:18087/mcp?project_id=codev-platform",
            "recall",
            {"query": "__runtime_acceptance__", "limit": 1},
        ),
    ]
    assert all(
        headers == {"Authorization": "Bearer never-in-receipt-token"}
        for _, headers, _, _ in calls
    )
    assert len(evidence.evidence_sha256) == 64
    assert "token" not in evidence.evidence_sha256


def test_passthrough模式不伪造Authorization头() -> None:
    captured: list[dict[str, str]] = []

    async def caller(
        _url: str,
        headers: dict[str, str],
        _tool: str,
        _arguments: dict[str, object],
    ) -> MCPToolResponse:
        captured.append(headers)
        return MCPToolResponse(False, (_successful_payload(_tool),))

    asyncio.run(
        verify_mcp_acceptance_async(
            _config(auth_mode="passthrough"),
            "codev-platform",
            endpoints=_ENDPOINTS,
            environment={},
            caller=caller,
        )
    )

    assert captured == [{}, {}, {}, {}]


@pytest.mark.parametrize(
    "response",
    (
        MCPToolResponse(True, ({"ok": True},)),
        MCPToolResponse(False, ()),
        MCPToolResponse(False, ({"error": "boom"},)),
        MCPToolResponse(False, ({"ok": False},)),
        MCPToolResponse(False, ({"ok": 0},)),
        MCPToolResponse(False, ({"status": "error"},)),
        MCPToolResponse(False, ({"failures": ["boom"]},)),
        MCPToolResponse(False, ({},)),
        MCPToolResponse(False, (["不是对象"],)),
    ),
)
def test_仅HTTP成功或错误结构都不能通过(response: MCPToolResponse) -> None:
    async def caller(
        _url: str,
        _headers: dict[str, str],
        _tool: str,
        _arguments: dict[str, object],
    ) -> MCPToolResponse:
        return response

    with pytest.raises(RuntimeDeploymentError, match="MCP 真实工具验收失败") as captured:
        asyncio.run(
            verify_mcp_acceptance_async(
                _config(),
                "codev-platform",
                endpoints=_ENDPOINTS,
                environment={"PLATFORM_TOKEN": "secret"},
                caller=caller,
            )
        )

    assert "boom" not in str(captured.value)
    assert "secret" not in str(captured.value)


@pytest.mark.parametrize(
    ("tool", "changes"),
    (
        ("list_collections", {"project_id": "other"}),
        ("codegraph_status", {"project_id": "other"}),
        ("codegraph_status", {"tool": "codegraph_search"}),
        ("search_nodes", {"project_id": "other"}),
        ("search_nodes", {"tool": "wrong"}),
        ("search_nodes", {"count": 1}),
        ("search_nodes", {"hits": [None], "count": 1}),
        ("recall", {"project_id": "other"}),
        ("recall", {"tool": "wrong"}),
        ("recall", {"entries": "invalid"}),
        ("recall", {"entries": [None], "count": 1}),
    ),
)
def test_各工具成功语义或回显漂移时失败关闭(
    tool: str,
    changes: dict[str, object],
) -> None:
    async def caller(
        _url: str,
        _headers: dict[str, str],
        current_tool: str,
        _arguments: dict[str, object],
    ) -> MCPToolResponse:
        payload = _successful_payload(current_tool)
        if current_tool == tool:
            payload = {**payload, **changes}
        return MCPToolResponse(False, (payload,))

    with pytest.raises(RuntimeDeploymentError, match="MCP 真实工具验收失败"):
        asyncio.run(
            verify_mcp_acceptance_async(
                _config(),
                "codev-platform",
                endpoints=_ENDPOINTS,
                environment={"PLATFORM_TOKEN": "secret"},
                caller=caller,
            )
        )


@pytest.mark.parametrize("tool", ("search_nodes", "recall"))
def test_Graph和Memory缺少路由身份回显时失败关闭(tool: str) -> None:
    async def caller(
        _url: str,
        _headers: dict[str, str],
        current_tool: str,
        _arguments: dict[str, object],
    ) -> MCPToolResponse:
        payload = _successful_payload(current_tool)
        if current_tool == tool:
            payload.pop("project_id")
        return MCPToolResponse(False, (payload,))

    with pytest.raises(RuntimeDeploymentError, match="MCP 真实工具验收失败"):
        asyncio.run(
            verify_mcp_acceptance_async(
                _config(),
                "codev-platform",
                endpoints=_ENDPOINTS,
                environment={"PLATFORM_TOKEN": "secret"},
                caller=caller,
            )
        )


def test_相同结构的不同业务值产生不同证据摘要() -> None:
    def run(total_chunks: int) -> str:
        async def caller(
            _url: str,
            _headers: dict[str, str],
            tool: str,
            _arguments: dict[str, object],
        ) -> MCPToolResponse:
            payload = _successful_payload(tool)
            if tool == "list_collections":
                payload = {**payload, "total_chunks": total_chunks}
            return MCPToolResponse(False, (payload,))

        return asyncio.run(
            verify_mcp_acceptance_async(
                _config(),
                "codev-platform",
                endpoints=_ENDPOINTS,
                environment={"PLATFORM_TOKEN": "secret"},
                caller=caller,
            )
        ).evidence_sha256

    assert run(1) != run(2)


def test_MCP服务视图只从工具调用注册表派生() -> None:
    assert mcp_acceptance.mcp_acceptance_services() == tuple(
        call.service for call in mcp_acceptance.MCP_ACCEPTANCE_CALLS
    )


def test_SDK适配拒绝静默忽略非文本回执() -> None:
    result = SimpleNamespace(
        isError=False,
        structuredContent=None,
        content=[
            SimpleNamespace(type="image", data="x" * (2 * 1024 * 1024)),
            SimpleNamespace(
                type="text",
                text=json.dumps(_successful_payload("search_nodes")),
            ),
        ],
    )

    with pytest.raises(RuntimeDeploymentError, match="SDK 工具回执无效"):
        mcp_acceptance._adapt_sdk_response(result)


def test_SDK适配拒绝双重表示和多个文本载荷() -> None:
    payload = _successful_payload("search_nodes")
    dual = SimpleNamespace(
        isError=False,
        structuredContent=payload,
        content=[SimpleNamespace(type="text", text=json.dumps(payload))],
    )
    multiple = SimpleNamespace(
        isError=False,
        structuredContent=None,
        content=[
            SimpleNamespace(type="text", text=json.dumps(payload)),
            SimpleNamespace(type="text", text=json.dumps(payload)),
        ],
    )

    for result in (dual, multiple):
        with pytest.raises(RuntimeDeploymentError, match="SDK 工具回执无效"):
            mcp_acceptance._adapt_sdk_response(result)


@pytest.mark.parametrize(
    "text",
    (
        (
            '{"project_id":"other","project_id":"codev-platform",'
            '"tool":"wrong","tool":"search_nodes","error":"boom","error":null}'
        ),
        '{"hits":[{"name":"bad","name":"good"}],"count":1}',
    ),
)
def test_SDK适配递归拒绝JSON重复键(text: str) -> None:
    result = SimpleNamespace(
        isError=False,
        structuredContent=None,
        content=[SimpleNamespace(type="text", text=text)],
    )

    with pytest.raises(RuntimeDeploymentError, match="文本不是 JSON"):
        mcp_acceptance._adapt_sdk_response(result)


def test_SDK适配在解析前限制唯一文本回执总量() -> None:
    result = SimpleNamespace(
        isError=False,
        structuredContent=None,
        content=[
            SimpleNamespace(
                type="text",
                text=json.dumps({"padding": "y" * (1024 * 1024)}),
            )
        ],
    )

    with pytest.raises(RuntimeDeploymentError, match="回执超过上限"):
        mcp_acceptance._adapt_sdk_response(result)


def test_验收拒绝调用端口注入多个相互冲突载荷() -> None:
    async def caller(
        _url: str,
        _headers: dict[str, str],
        tool: str,
        _arguments: dict[str, object],
    ) -> MCPToolResponse:
        payload = _successful_payload(tool)
        return MCPToolResponse(False, (payload, payload))

    with pytest.raises(RuntimeDeploymentError, match="MCP 真实工具验收失败"):
        asyncio.run(
            verify_mcp_acceptance_async(
                _config(),
                "codev-platform",
                endpoints=_ENDPOINTS,
                environment={"PLATFORM_TOKEN": "secret"},
                caller=caller,
            )
        )


def test_认证token变化不改变相同业务回执的证据摘要() -> None:
    def run(token: str) -> str:
        async def caller(
            _url: str,
            _headers: dict[str, str],
            tool: str,
            _arguments: dict[str, object],
        ) -> MCPToolResponse:
            return MCPToolResponse(False, (_successful_payload(tool),))

        return asyncio.run(
            verify_mcp_acceptance_async(
                _config(),
                "codev-platform",
                endpoints=_ENDPOINTS,
                environment={"PLATFORM_TOKEN": token},
                caller=caller,
            )
        ).evidence_sha256

    assert run("token-one") == run("token-two")


def test_调用端口异常即使包含token也只返回统一脱敏错误() -> None:
    async def caller(
        _url: str,
        headers: dict[str, str],
        _tool: str,
        _arguments: dict[str, object],
    ) -> MCPToolResponse:
        raise RuntimeError(f"下游异常：{headers['Authorization']}")

    with pytest.raises(RuntimeDeploymentError, match="MCP 真实工具验收失败") as captured:
        asyncio.run(
            verify_mcp_acceptance_async(
                _config(),
                "codev-platform",
                endpoints=_ENDPOINTS,
                environment={"PLATFORM_TOKEN": "sensitive-token"},
                caller=caller,
            )
        )

    assert "sensitive-token" not in str(captured.value)
    assert "Authorization" not in str(captured.value)


def test_token模式缺少受管环境变量时在联网前失败() -> None:
    called = False

    async def caller(*_args: object) -> MCPToolResponse:
        nonlocal called
        called = True
        return MCPToolResponse(False, ({"ok": True},))

    with pytest.raises(RuntimeDeploymentError, match="认证环境"):
        asyncio.run(
            verify_mcp_acceptance_async(
                _config(),
                "codev-platform",
                endpoints=_ENDPOINTS,
                environment={},
                caller=caller,
            )
        )

    assert called is False


def test_端点集合缺失重复或端口越界时失败关闭() -> None:
    async def caller(*_args: object) -> MCPToolResponse:
        raise AssertionError("非法端点不应联网")

    invalid_sets = (
        _ENDPOINTS[:-1],
        (*_ENDPOINTS[:-1], _ENDPOINTS[0]),
    )
    for endpoints in invalid_sets:
        with pytest.raises(RuntimeDeploymentError):
            asyncio.run(
                verify_mcp_acceptance_async(
                    _config(auth_mode="passthrough"),
                    "codev-platform",
                    endpoints=endpoints,
                    environment={},
                    caller=caller,
                )
            )
    with pytest.raises(RuntimeDeploymentError):
        MCPServiceEndpoint("graph", 65536)


def test_调用契约类型拒绝动态服务名和可变参数() -> None:
    with pytest.raises(RuntimeDeploymentError):
        MCPAcceptanceCall("other", "status", {})
    with pytest.raises(RuntimeDeploymentError):
        MCPServiceEndpoint("platform-docs", True)
