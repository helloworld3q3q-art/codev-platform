"""四套本机 MCP 的 Streamable HTTP 工具级验收。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from types import MappingProxyType
from urllib.parse import quote

from codev_platform.core.config import get as config_get
from codev_platform.runtime_deployment_contract import RuntimeDeploymentError


_PROJECT_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}\Z")
_ENVIRONMENT_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,127}\Z")
_TOKEN_ENV_FALLBACKS = ("PLATFORM_TOKEN", "CODEV_PLATFORM_MCP_TOKEN")
_MAX_RECEIPT_BYTES = 1024 * 1024
_REQUEST_TIMEOUT_SEC = 30.0


def _validate_common_payload(payload: dict[str, object], _project_id: str) -> None:
    if payload.get("error") not in {None, "", False}:
        raise RuntimeDeploymentError("MCP 工具返回错误对象")
    if "ok" in payload and payload["ok"] is not True:
        raise RuntimeDeploymentError("MCP 工具返回错误对象")
    if "status" in payload and payload["status"] not in {
        "ok",
        "healthy",
        "running",
        "success",
    }:
        raise RuntimeDeploymentError("MCP 工具返回错误对象")
    if "failures" in payload and (
        type(payload["failures"]) is not list or bool(payload["failures"])
    ):
        raise RuntimeDeploymentError("MCP 工具返回错误对象")


def _valid_counter(value: object) -> bool:
    return type(value) is dict and all(
        type(key) is str and type(count) is int and count >= 0
        for key, count in value.items()
    )


def _validate_platform_docs_payload(
    payload: dict[str, object],
    project_id: str,
) -> None:
    if (
        payload.get("project_id") != project_id
        or type(payload.get("collection")) is not str
        or not payload["collection"]
        or type(payload.get("total_chunks")) is not int
        or payload["total_chunks"] < 0
        or not _valid_counter(payload.get("by_category"))
        or not _valid_counter(payload.get("by_module"))
    ):
        raise RuntimeDeploymentError("platform-docs 验收回执无效")


def _validate_codegraph_payload(
    payload: dict[str, object],
    project_id: str,
) -> None:
    repos = payload.get("repos")
    if (
        payload.get("project_id") != project_id
        or payload.get("tool") != "codegraph_status"
        or payload.get("merge") != "codev-fanout-v1"
        or type(repos) is not list
        or not repos
        or payload.get("failures") != []
    ):
        raise RuntimeDeploymentError("CodeGraph 验收回执无效")
    for repo in repos:
        if type(repo) is not dict:
            raise RuntimeDeploymentError("CodeGraph 验收回执无效")
        content = repo.get("content")
        if (
            type(repo.get("repo")) is not str
            or not repo["repo"]
            or repo.get("ok") is not True
            or repo.get("is_error") is not False
            or type(content) is not list
            or not content
            or not all(
                type(item) is dict
                and item.get("type") == "text"
                and type(item.get("text")) is str
                and bool(item["text"])
                for item in content
            )
        ):
            raise RuntimeDeploymentError("CodeGraph 验收回执无效")


def _validate_counted_payload(
    payload: dict[str, object],
    *,
    items_field: str,
) -> None:
    items = payload.get(items_field)
    count = payload.get("count")
    if (
        type(items) is not list
        or type(count) is not int
        or count < 0
        or count != len(items)
        or not all(type(item) is dict and bool(item) for item in items)
    ):
        raise RuntimeDeploymentError("MCP 计数回执无效")


def _validate_graph_payload(payload: dict[str, object], project_id: str) -> None:
    if (
        payload.get("project_id") != project_id
        or payload.get("tool") != "search_nodes"
        or payload.get("query") != "__runtime_acceptance__"
        or payload.get("kind") != "all"
    ):
        raise RuntimeDeploymentError("Graph 验收回执无效")
    _validate_counted_payload(payload, items_field="hits")


def _validate_memory_payload(payload: dict[str, object], project_id: str) -> None:
    if (
        payload.get("project_id") != project_id
        or payload.get("tool") != "recall"
        or payload.get("query") != "__runtime_acceptance__"
    ):
        raise RuntimeDeploymentError("Agent Memory 验收回执无效")
    _validate_counted_payload(payload, items_field="entries")


PayloadValidator = Callable[[dict[str, object], str], None]


@dataclass(frozen=True, slots=True)
class _MCPCallSpec:
    """固定工具调用及其成功语义校验策略。"""

    tool: str
    arguments: dict[str, object]
    validate_payload: PayloadValidator


_CALL_SPECS: dict[str, _MCPCallSpec] = {
    "platform-docs": _MCPCallSpec(
        "list_collections",
        {},
        _validate_platform_docs_payload,
    ),
    "codegraph": _MCPCallSpec(
        "codegraph_status",
        {"_codev_merge": "structured"},
        _validate_codegraph_payload,
    ),
    "graph": _MCPCallSpec(
        "search_nodes",
        {"query": "__runtime_acceptance__", "limit": 1},
        _validate_graph_payload,
    ),
    "agent-memory": _MCPCallSpec(
        "recall",
        {"query": "__runtime_acceptance__", "limit": 1},
        _validate_memory_payload,
    ),
}


@dataclass(frozen=True, slots=True)
class MCPAcceptanceCall:
    """固定服务与固定只读工具参数，禁止调用方扩展任意操作。"""

    service: str
    tool: str
    arguments: Mapping[str, object]

    def __post_init__(self) -> None:
        expected = _CALL_SPECS.get(self.service)
        if (
            expected is None
            or type(self.tool) is not str
            or type(self.arguments) is not dict
            or self.tool != expected.tool
            or self.arguments != expected.arguments
        ):
            raise RuntimeDeploymentError("MCP 验收调用契约无效")
        object.__setattr__(self, "arguments", MappingProxyType(dict(self.arguments)))


MCP_ACCEPTANCE_CALLS = tuple(
    MCPAcceptanceCall(service, spec.tool, spec.arguments)
    for service, spec in _CALL_SPECS.items()
)


def mcp_acceptance_services() -> tuple[str, ...]:
    """返回与真实工具调用严格同序的不可变服务视图。"""
    return tuple(call.service for call in MCP_ACCEPTANCE_CALLS)


@dataclass(frozen=True, slots=True)
class MCPServiceEndpoint:
    """只允许把受管服务映射到本机 TCP 端口。"""

    name: str
    port: int

    def __post_init__(self) -> None:
        if (
            type(self.name) is not str
            or self.name not in mcp_acceptance_services()
            or type(self.port) is not int
            or not 1 <= self.port <= 65535
        ):
            raise RuntimeDeploymentError("MCP 验收端点无效")


@dataclass(frozen=True, slots=True)
class MCPToolResponse:
    """把 SDK 返回收窄为错误位与已解码 JSON 载荷。"""

    is_error: bool
    payloads: tuple[object, ...]

    def __post_init__(self) -> None:
        if type(self.is_error) is not bool or type(self.payloads) is not tuple:
            raise RuntimeDeploymentError("MCP 工具回执类型无效")


@dataclass(frozen=True, slots=True)
class MCPAcceptanceEvidence:
    """四项成功结构的脱敏聚合摘要。"""

    evidence_sha256: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", self.evidence_sha256):
            raise RuntimeDeploymentError("MCP 验收证据无效")


MCPToolCaller = Callable[
    [str, dict[str, str], str, dict[str, object]],
    Awaitable[MCPToolResponse],
]


async def verify_mcp_acceptance_async(
    cfg: dict[str, object],
    project_id: str,
    *,
    endpoints: tuple[MCPServiceEndpoint, ...] | None = None,
    environment: Mapping[str, str] | None = None,
    caller: MCPToolCaller | None = None,
) -> MCPAcceptanceEvidence:
    """逐一初始化会话并调用固定工具；HTTP 可达本身不算成功。"""
    if type(cfg) is not dict or type(project_id) is not str or _PROJECT_ID.fullmatch(project_id) is None:
        raise RuntimeDeploymentError("MCP 验收输入无效")
    selected = _default_endpoints(cfg) if endpoints is None else endpoints
    indexed = _validated_endpoints(selected)
    headers = _authorization_headers(cfg, os.environ if environment is None else environment)
    active_caller = default_mcp_tool_caller if caller is None else caller
    if not callable(active_caller):
        raise RuntimeDeploymentError("MCP 验收调用端口不可用")

    evidence: list[str] = []
    for call in MCP_ACCEPTANCE_CALLS:
        endpoint = indexed[call.service]
        url = _streamable_url(endpoint, project_id)
        try:
            response = await active_caller(
                url,
                dict(headers),
                call.tool,
                dict(call.arguments),
            )
            evidence.append(_validate_response(call, response, project_id))
        except (KeyboardInterrupt, SystemExit, MemoryError):
            raise
        except Exception:
            raise RuntimeDeploymentError("MCP 真实工具验收失败") from None
    digest = hashlib.sha256("".join(f"{item}\n" for item in evidence).encode("ascii")).hexdigest()
    return MCPAcceptanceEvidence(digest)


def verify_mcp_acceptance(
    cfg: dict[str, object],
    project_id: str,
) -> MCPAcceptanceEvidence:
    """同步部署叶子入口；部署进程不允许嵌套事件循环。"""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(verify_mcp_acceptance_async(cfg, project_id))
    raise RuntimeDeploymentError("MCP 同步验收不能在运行中的事件循环内调用")


async def default_mcp_tool_caller(
    url: str,
    headers: dict[str, str],
    tool: str,
    arguments: dict[str, object],
) -> MCPToolResponse:
    """用禁代理、禁重定向、有界超时的 SDK 客户端完成一次真实调用。"""
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    timeout = httpx.Timeout(
        _REQUEST_TIMEOUT_SEC,
        connect=5.0,
        pool=5.0,
    )
    async with httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        trust_env=False,
        follow_redirects=False,
    ) as http_client:
        async with streamable_http_client(url, http_client=http_client) as streams:
            read_stream, write_stream, _session_id = streams
            async with ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=_REQUEST_TIMEOUT_SEC),
            ) as session:
                await session.initialize()
                result = await session.call_tool(
                    tool,
                    arguments,
                    read_timeout_seconds=timedelta(seconds=_REQUEST_TIMEOUT_SEC),
                )
    return _adapt_sdk_response(result)


def _default_endpoints(cfg: dict[str, object]) -> tuple[MCPServiceEndpoint, ...]:
    from codev_platform.mcp_serve import iter_endpoints

    try:
        return tuple(MCPServiceEndpoint(item.name, item.port) for item in iter_endpoints(cfg))
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise RuntimeDeploymentError("MCP 验收端点无法从受管配置解析") from None


def _validated_endpoints(
    endpoints: tuple[MCPServiceEndpoint, ...],
) -> dict[str, MCPServiceEndpoint]:
    if type(endpoints) is not tuple or not all(type(item) is MCPServiceEndpoint for item in endpoints):
        raise RuntimeDeploymentError("MCP 验收端点集合无效")
    indexed = {item.name: item for item in endpoints}
    services = mcp_acceptance_services()
    if len(endpoints) != len(services) or set(indexed) != set(services):
        raise RuntimeDeploymentError("MCP 验收端点集合不完整或重复")
    return indexed


def _authorization_headers(
    cfg: dict[str, object],
    environment: Mapping[str, str],
) -> dict[str, str]:
    if not isinstance(environment, Mapping):
        raise RuntimeDeploymentError("MCP 认证环境无效")
    mode = config_get(cfg, "gateway.auth_mode", "passthrough")
    if mode == "passthrough":
        return {}
    if mode != "token":
        raise RuntimeDeploymentError("MCP 认证模式无效")
    configured = config_get(cfg, "platform.token_env")
    names: list[str] = []
    if configured is not None:
        if type(configured) is not str or _ENVIRONMENT_NAME.fullmatch(configured) is None:
            raise RuntimeDeploymentError("MCP 认证环境变量名无效")
        names.append(configured)
    names.extend(name for name in _TOKEN_ENV_FALLBACKS if name not in names)
    for name in names:
        token = environment.get(name)
        if _valid_bearer_token(token):
            return {"Authorization": f"Bearer {token}"}
    raise RuntimeDeploymentError("MCP token 认证环境缺失")


def _valid_bearer_token(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= 8192
        and all(33 <= ord(char) <= 126 for char in value)
    )


def _streamable_url(endpoint: MCPServiceEndpoint, project_id: str) -> str:
    encoded = quote(project_id, safe="")
    return f"http://127.0.0.1:{endpoint.port}/mcp?project_id={encoded}"


def _validate_response(
    call: MCPAcceptanceCall,
    response: MCPToolResponse,
    project_id: str,
) -> str:
    if (
        type(response) is not MCPToolResponse
        or response.is_error
        or len(response.payloads) != 1
    ):
        raise RuntimeDeploymentError("MCP 工具未返回结构化成功")
    spec = _CALL_SPECS[call.service]
    for payload in response.payloads:
        if type(payload) is not dict or not all(type(key) is str for key in payload):
            raise RuntimeDeploymentError("MCP 工具返回非对象结构")
        _validate_common_payload(payload, project_id)
        spec.validate_payload(payload, project_id)
    normalized = {
        "payloads": list(response.payloads),
        "project_id": project_id,
        "service": call.service,
        "tool": call.tool,
    }
    encoded = _encode_json_receipt(normalized, error_message="MCP 工具回执无法规范化")
    if len(encoded) > _MAX_RECEIPT_BYTES:
        raise RuntimeDeploymentError("MCP 工具回执超过上限")
    return hashlib.sha256(encoded).hexdigest()


def _encode_json_receipt(value: object, *, error_message: str) -> bytes:
    """把 JSON 回执编码为确定字节，供总量门禁和证据摘要共用。"""
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError):
        raise RuntimeDeploymentError(error_message) from None


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """递归构造 JSON 对象并拒绝后值覆盖前值的重复键。"""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 对象存在重复键")
        result[key] = value
    return result


def _adapt_sdk_response(result: object) -> MCPToolResponse:
    is_error = getattr(result, "isError", None)
    content = getattr(result, "content", None)
    structured = getattr(result, "structuredContent", None)
    if (
        type(is_error) is not bool
        or structured is not None
        or type(content) is not list
        or len(content) != 1
    ):
        raise RuntimeDeploymentError("MCP SDK 工具回执无效")
    payloads: list[object] = []
    receipt_bytes = 0
    for item in content:
        if getattr(item, "type", None) != "text":
            raise RuntimeDeploymentError("MCP SDK 工具回执无效")
        text = getattr(item, "text", None)
        if type(text) is not str or not text:
            raise RuntimeDeploymentError("MCP 工具文本回执无效")
        receipt_bytes += len(text.encode("utf-8"))
        if receipt_bytes > _MAX_RECEIPT_BYTES:
            raise RuntimeDeploymentError("MCP 工具回执超过上限")
        try:
            payloads.append(
                json.loads(
                    text,
                    object_pairs_hook=_unique_json_object,
                )
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            raise RuntimeDeploymentError("MCP 工具文本不是 JSON") from None
    return MCPToolResponse(is_error, tuple(payloads))


__all__ = [
    "MCP_ACCEPTANCE_CALLS",
    "MCPAcceptanceCall",
    "MCPAcceptanceEvidence",
    "MCPServiceEndpoint",
    "MCPToolResponse",
    "default_mcp_tool_caller",
    "mcp_acceptance_services",
    "verify_mcp_acceptance",
    "verify_mcp_acceptance_async",
]
