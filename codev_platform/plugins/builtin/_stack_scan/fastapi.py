"""FastAPI 后端栈扫描: detect + AST 扫 @router.<method> -> backend_endpoint。"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from codev_platform.graph.schema import (
    GraphNode,
    NodeKind,
)

from ._common import (
    _iter_files,
    _norm_url,
    _rel,
    logger,
)

_HTTP_METHODS = {"get", "post", "put", "delete", "patch"}


def fastapi_detect(repo: Path) -> bool:
    """有 FastAPI 迹象即命中: 某 .py 含 'import fastapi' / 'from fastapi' 或 @router.<method>。"""
    router_dec = re.compile(r"@\w+\.(?:get|post|put|delete|patch)\s*\(")
    for f in _iter_files(repo, (".py",)):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if ("import fastapi" in text or "from fastapi" in text
                or router_dec.search(text)):
            return True
    return False


def _route_from_decorator(dec: ast.expr) -> tuple[str, str] | None:
    if not isinstance(dec, ast.Call):
        return None
    func = dec.func
    if not (isinstance(func, ast.Attribute) and func.attr in _HTTP_METHODS):
        return None
    for arg in dec.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return func.attr.upper(), _norm_url(arg.value)
    return None


def scan_fastapi(repo: Path, project_id: str) -> list[GraphNode]:
    """AST 扫 @router.<method>("/...") -> backend_endpoint 节点。

    name = operation_id (有则用, 对齐 openapi 风格), 否则 handler 函数名。
    """
    nodes: list[GraphNode] = []
    seen: set[str] = set()
    for f in _iter_files(repo, (".py",)):
        try:
            src = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if "@" not in src:  # 廉价短路: 无装饰器文件直接跳过。
            continue
        rel = _rel(f, repo)
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            logger.warning("parse fail %s: %s", rel, exc)
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                parsed = _route_from_decorator(dec)
                if not parsed:
                    continue
                method, url = parsed
                op_id = None
                if isinstance(dec, ast.Call):
                    for kw in dec.keywords:
                        if kw.arg == "operation_id" and isinstance(
                            kw.value, ast.Constant
                        ):
                            op_id = kw.value.value
                name = op_id or node.name
                node_id = f"{project_id}:backend_endpoint:{method}:{url}"
                if node_id in seen:
                    continue
                seen.add(node_id)
                nodes.append(
                    GraphNode(
                        id=node_id,
                        kind=NodeKind.BACKEND_ENDPOINT.value,
                        name=name,
                        project_id=project_id,
                        file=rel,
                        line=node.lineno,
                        language="python",
                        meta={
                            "url": url,
                            "http_method": method,
                            "handler": node.name,
                        },
                    )
                )
    return nodes
