"""前端 API 使用归因解析引擎 —— 页面/组件 --uses_api--> 它实际调用的 frontend_api_call。

**为什么**: 集中声明 API 的项目(URL.js: `export const PICK_CHECK_TURN = ...`)里, "页面 import
整个注册模块" ≠ "调用其每个接口"。需把端点的调用方精确归因到**真正用了某 api_call 的页面**,
取代"经共享模块 contains 泛连"的过报(见 graph.impact.build_impact_graph 对 contains 的过滤)。

**设计(策略式, 可扩展 + 低耦合 + 解析高效)**:
- 不同前端有不同"使用模式", 已知两种真实形态:
    ① url_registry 常量按名引用(PDA / 企业前端): 页面写 `URLRoot.PICK_CHECK_TURN`。
    ② 服务方法封装调用(量化 stockapi.ts 的 `getStockList()`): 页面调方法名 —— **后续策略**。
  每种是一个 `UsageResolver`; 新增模式 = 加一个 resolver + 注册一行, **不改驱动与既有策略**。
- **每文件源码只读一次**: 驱动统一读 + tokenize, 各策略复用同一 token 集(避免 N 策略 × 全仓 IO)。
- 纯计算, IO 经 `read_source` 注入 → 脱 store / repo 可单测。
"""
from __future__ import annotations

import re
from typing import Protocol
from collections.abc import Callable

from codev_platform.graph.schema import EdgeKind, GraphEdge, GraphNode, NodeKind

# 源码标识符 token: 一次正则切出全部标识符, 供"引用了哪些已知名字"做集合相交(O(token), 不做 N×M)。
_RE_IDENT = re.compile(r"[A-Za-z_$][\w$]*")

_SOURCE_NODE_KINDS = (NodeKind.FRONTEND_COMPONENT.value, NodeKind.FRONTEND_MODULE.value)


def _identifier_tokens(text: str) -> set[str]:
    return {m.group(0) for m in _RE_IDENT.finditer(text)}


def _index_source_files(nodes: list[GraphNode]) -> dict[str, str]:
    """file -> 源节点 id(页面/组件)。同文件多节点时优先 frontend_component(语义更强)。"""
    out: dict[str, str] = {}
    for n in nodes:
        if n.kind in _SOURCE_NODE_KINDS and n.file:
            cur = out.get(n.file)
            if cur is None or n.kind == NodeKind.FRONTEND_COMPONENT.value:
                out[n.file] = n.id
    return out


class UsageResolver(Protocol):
    """一种前端 API 使用模式的归因策略(两阶段, 为驱动"每文件只读一次"服务)。

    prepare(nodes): 从全量节点建索引; 返回是否有可归因目标(无则驱动跳过该策略)。
    edges_for_file(file, src_id, tokens): 拿某文件的标识符集合 → 产 uses_api 边(纯计算)。
    """

    name: str

    def prepare(self, nodes: list[GraphNode]) -> bool: ...

    def edges_for_file(self, file: str, src_id: str, tokens: set[str]) -> list[GraphEdge]: ...


# url_registry 常量名"像真标识符"的判据: 全大写蛇形 ≥4 字符(GET_NEW_VERSION / PICK_CHECK_TURN)。
# 滤掉 minified bundle 的噪声名(单字母 j/D/N、urls 等)→ 不拿它们去满仓乱匹配制造假 uses 边。
_RE_REAL_CONST = re.compile(r"^[A-Z][A-Z0-9_]{3,}$")


class ConstantReferenceUsageResolver:
    """模式①: url_registry 常量按名引用。页面源码出现常量名(`PICK_CHECK_TURN`)→ 用了该 api_call。

    只认 `url_registry=True` 且名像真标识符的 api_call → 内联 api_call(量化项目)零影响。
    排除常量自身的声明文件(URL.js 里全是声明, 不是"使用方")。
    """

    name = "url_registry_const_ref"

    def __init__(self) -> None:
        self._name_to_api: dict[str, str] = {}
        self._decl_file: dict[str, str] = {}
        self._names: set[str] = set()

    def prepare(self, nodes: list[GraphNode]) -> bool:
        for n in nodes:
            if n.kind == NodeKind.FRONTEND_API_CALL.value and (n.meta or {}).get("url_registry"):
                nm = n.name or ""
                if _RE_REAL_CONST.match(nm):
                    self._name_to_api.setdefault(nm, n.id)
                    self._decl_file[n.id] = n.file or ""
        self._names = set(self._name_to_api)
        return bool(self._names)

    def edges_for_file(self, file: str, src_id: str, tokens: set[str]) -> list[GraphEdge]:
        out: list[GraphEdge] = []
        for nm in tokens & self._names:
            api_id = self._name_to_api[nm]
            if file == self._decl_file.get(api_id):   # 声明文件自身不是"使用方"
                continue
            out.append(GraphEdge(source=src_id, target=api_id,
                                 kind=EdgeKind.USES_API.value, confidence=1.0))
        return out


def _build_resolvers() -> list[UsageResolver]:
    """注册表: 新增使用模式在此加一行(如未来的 ServiceMethodCallUsageResolver)。"""
    return [ConstantReferenceUsageResolver()]


def resolve_api_usage_edges(
    nodes: list[GraphNode], read_source: Callable[[str], str]
) -> list[GraphEdge]:
    """驱动: 跑全部适用 UsageResolver, 产 page→api_call 精确 uses_api 边。源码每文件只读一次。

    无适用策略(纯内联项目, 无 url_registry 常量)→ 返回 []。(source,target,kind)去重。
    """
    resolvers = [r for r in _build_resolvers() if r.prepare(nodes)]
    if not resolvers:
        return []
    file_node = _index_source_files(nodes)
    edges: list[GraphEdge] = []
    seen: set[tuple[str, str, str]] = set()
    for file, src_id in file_node.items():
        text = read_source(file)
        if not text:
            continue
        tokens = _identifier_tokens(text)
        for r in resolvers:
            for e in r.edges_for_file(file, src_id, tokens):
                key = (e.source, e.target, e.kind)
                if key in seen:
                    continue
                seen.add(key)
                edges.append(e)
    return edges
