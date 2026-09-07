"""Node/Express 后端栈扫描: detect + 正则扫路由注册 -> backend_endpoint。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from codev_platform.graph.schema import (
    GraphNode,
    NodeKind,
)

from ._common import (
    _iter_files,
    _iter_named,
    _norm_url,
    _rel,
    logger,
)

# 后端框架特征依赖 (出现在 package.json deps 即命中本栈)。
_NODE_BACKEND_DEPS = frozenset({"express", "koa", "fastify", "@koa/router"})

# app.get('/x', ...) / router.post('/x', ...) / api.use('/x', ...) 路由注册。
# 捕获 (对象名, 方法, url) —— 方法限 HTTP 动词 + use (中间件挂载点)。
_RE_NODE_ROUTE = re.compile(
    r"""(?P<obj>\w+)\s*\.\s*(?P<method>get|post|put|delete|patch|options|head|all|use)\s*\(\s*[`'\"](?P<url>/[\w\-/:.{}]*)"""
)
# 限定 obj 必须像路由对象 (app / router / api / route / *Router / *router),
# 过滤掉 lodash.get / array.use 之类误命中。
_NODE_ROUTE_OBJ = re.compile(r"^(?:app|router|api|route)$|[Rr]outer$", re.A)


def node_detect(repo: Path) -> bool:
    """有 Node/Express 系后端迹象即命中: package.json deps 含 express/koa/fastify。

    纯按 repo 内容判定 (查依赖, 不靠目录名), 多框架可共存 (与 react_detect 同一仓不冲突)。
    """
    for pkg in _iter_named(repo, "package.json"):
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        deps: dict = {}
        for key in ("dependencies", "devDependencies"):
            section = data.get(key)
            if isinstance(section, dict):
                deps.update(section)
        if _NODE_BACKEND_DEPS & set(deps):
            return True
    return False


def scan_node_express(repo: Path, project_id: str) -> list[GraphNode]:
    """正则扫 Express/Koa/Fastify 路由注册 -> backend_endpoint 节点。

    匹配 app.<method>('/url', ...) / router.<method>('/url', ...) / api.use('/url', ...)。
    method 限 HTTP 动词 + use (中间件挂载点, 记 http_method=USE)。第一版轻量正则,
    不解析 router 挂载前缀 (app.use('/api', router) 的路径拼接), plan 允许不追求全覆盖。
    node id 与 FastAPI 同构 "<pid>:backend_endpoint:<METHOD>:<url>", 跨插件可链接。
    """
    nodes: list[GraphNode] = []
    seen: set[str] = set()
    for f in _iter_files(repo, (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
        if f.name in {"typings.d.ts"}:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("read fail %s: %s", f, exc)
            continue
        rel = _rel(f, repo)
        for m in _RE_NODE_ROUTE.finditer(text):
            obj = m.group("obj")
            if not _NODE_ROUTE_OBJ.search(obj):
                continue
            method = m.group("method").upper()
            url = _norm_url(m.group("url")) or "/"
            line = text.count("\n", 0, m.start()) + 1
            node_id = f"{project_id}:backend_endpoint:{method}:{url}"
            if node_id in seen:
                continue
            seen.add(node_id)
            nodes.append(
                GraphNode(
                    id=node_id,
                    kind=NodeKind.BACKEND_ENDPOINT.value,
                    name=f"{method} {url}",
                    project_id=project_id,
                    file=rel,
                    line=line,
                    language="typescript",
                    meta={
                        "url": url,
                        "http_method": method,
                        "router_obj": obj,
                    },
                )
            )
    return nodes
