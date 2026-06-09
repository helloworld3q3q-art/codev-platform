"""FastAPI / Python 调用边 resolver —— 补 codegraph 对 Python DI 的盲区。

codegraph 要追 Python 跨函数调用 self._store.x(), 必须先解析 self._store 的真实注入类型(DI),
动态分发追不动 → endpoint→碰表函数 的链在 DI 处断。本 resolver **放弃精确类型解析**, 改用
**方法名 BFS**: 从 endpoint handler 出发, 沿 AST 提取的「被调用方法名」逐层下钻
(self._store.upsert_x() 的 upsert_x 直接进调用名集合, 天然穿透 DI), 命中碰表 backend_function
(sql plugin 产)即连 endpoint→function 边。

权衡: 不解析类型 → 同名方法可能误连(已用通用名黑名单 + 深度收紧压低假阳性)。confidence=0.65
**低于** codegraph 兜底(0.7): codegraph 能精确解析的普通调用让它赢(去重按 confidence), 本
resolver 只在 codegraph 追不到的 DI 盲区(self._store.x())补空白边。留余量给未来类型推断精确版
(可提到 0.9+)。第一版轻量(plan unified-graph-lineage 允许不追求全覆盖)。
"""
from __future__ import annotations

import ast
from pathlib import Path

from codev_platform.graph.call_resolvers._bfs import build_call_edges
from codev_platform.graph.schema import GraphEdge, GraphNode, NodeKind, ProvSource

_NAME = "fastapi"
_PROV_SOURCE = ProvSource.REGEX.value  # 名称启发式 BFS(DI 盲区): 候选边, 非确定依赖
_CONF = 0.65  # < codegraph 0.7: 精确解析优先, 本 resolver 只补 codegraph 的 DI 盲区空白
_MAX_DEPTH = 3      # 真实 DI 链 handler→service.method→self._store.upsert(碰表) 仅 2-3 跳;
                    # 全局名字合并下深度越大越易跨模块串台(假阳性), 故收紧到 3。
_MAX_VISIT = 500    # 单 handler 可达方法名上限(防调用图爆炸)
_PY_LANGS = ("python", "py")
_SKIP_DIRS = frozenset({".venv", "venv", "__pycache__", "node_modules", ".git",
                        "build", "dist", ".mypy_cache", ".pytest_cache"})

# 通用容器 / str / io / re / dunder 方法名: 零区分度且满天飞(d.get() 与 store.get() 静态
# 不可分)。把它们当调用边锚点会让 healthCheck 牵连一堆碰表函数 → 过连。故 collector 层
# 直接不记录(既不作 BFS 下钻、也不作连边 target)。代价: 碰表函数若真叫裸 `get`/`update`
# 会漏(罕见, 由 codegraph 兜底)。宁漏勿滥 —— 影响分析最怕假阳性。
_STOP_NAMES = frozenset({
    # dict / list / set 容器
    "get", "keys", "values", "items", "append", "extend", "insert", "pop",
    "remove", "clear", "copy", "add", "discard", "setdefault", "index", "count",
    "sort", "reverse",
    # str
    "join", "split", "rsplit", "strip", "lstrip", "rstrip", "replace", "format",
    "format_map", "encode", "decode", "startswith", "endswith", "lower", "upper",
    "title", "splitlines", "zfill", "ljust", "rjust",
    # io / file
    "read", "write", "close", "flush", "seek", "readline", "readlines",
    # re / 常见库
    "group", "groups", "match", "search", "sub", "findall", "finditer", "compile",
    # dunder
    "__init__", "__enter__", "__exit__", "__call__", "__post_init__", "__str__",
})


class FastApiCallResolver:
    """Python endpoint handler --calls--> 碰表 backend_function(穿透 DI, 方法名 BFS)。"""

    name = _NAME
    prov_source = _PROV_SOURCE

    def applies(self, repo: Path, nodes: list[GraphNode]) -> bool:
        kinds = {n.kind for n in nodes}
        if NodeKind.BACKEND_FUNCTION.value not in kinds:
            return False  # 无碰表函数 → 无下钻终点, 不跑。
        # 有 python 端点才值得跑(纯 java 仓交给 spring / codegraph)。
        return any(_is_py_endpoint(n) for n in nodes)

    def resolve(self, repo: Path, project_id: str, nodes: list[GraphNode]) -> list[GraphEdge]:
        endpoints = [n for n in nodes if _is_py_endpoint(n)]
        tablefns = [n for n in nodes if n.kind == NodeKind.BACKEND_FUNCTION.value]
        if not endpoints or not tablefns:
            return []
        calls_by_func = _scan_calls(repo)
        return build_call_edges(
            endpoints, tablefns, calls_by_func,
            resolver=_NAME, confidence=_CONF, max_depth=_MAX_DEPTH, max_visit=_MAX_VISIT,
        )


def _is_py_endpoint(n: GraphNode) -> bool:
    return (n.kind == NodeKind.BACKEND_ENDPOINT.value
            and (n.language or "").lower() in _PY_LANGS)


def _call_name(func: ast.expr) -> str | None:
    """ast.Call.func -> 被调方法名: self._store.upsert_x -> "upsert_x"; helper() -> "helper"。"""
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


class _CallCollector(ast.NodeVisitor):
    """单文件遍历: 收集「函数名 -> 其体内被调用方法名集合」。嵌套调用归最内层函数。"""

    def __init__(self) -> None:
        self.calls_by_func: dict[str, set[str]] = {}
        self._stack: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Call(self, node: ast.Call) -> None:
        if self._stack:
            name = _call_name(node.func)
            if name and name not in _STOP_NAMES:  # 跳通用名(零区分度, 防过连)
                self.calls_by_func.setdefault(self._stack[-1], set()).add(name)
        self.generic_visit(node)


def _scan_calls(repo: Path) -> dict[str, set[str]]:
    """扫 repo 所有 .py, 建全局「函数名 -> 调用名集合」(跨文件同名函数合并)。

    按**名字**而非 (file, name) 建图, 是穿透 DI 的关键: service 方法在别的文件定义, 名字命中
    即能继续下钻, 无需解析 self._store 的真实类型。代价是同名函数被合并(轻微过连, conf 已降权)。
    """
    merged: dict[str, set[str]] = {}
    for f in repo.rglob("*.py"):
        if _SKIP_DIRS & set(f.parts):
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            continue  # 读失败 / 语法错(py 版本差异)→ 跳过该文件, 不拖垮。
        collector = _CallCollector()
        collector.visit(tree)
        for name, callees in collector.calls_by_func.items():
            merged.setdefault(name, set()).update(callees)
    return merged


def register_into() -> None:
    from codev_platform.graph.call_resolvers.base import register_resolver
    register_resolver(FastApiCallResolver())
