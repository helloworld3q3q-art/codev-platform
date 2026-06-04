"""React 前端栈扫描: detect + services/apis 调用 + 页面组件 calls_api 边。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from codev_platform.graph.schema import (
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)

from ._common import (
    _RE_INLINE_URL,
    _has_file_with_suffix,
    _iter_files,
    _iter_named,
    _norm_url,
    _rel,
    logger,
)

# axios / fetch / 项目 fetch 封装 / 生成的 services 里的 url 字面量。
_RE_FN = re.compile(r"export\s+async\s+function\s+(\w+)\s*\(")
_RE_URL = re.compile(
    r"url:\s*[`'\"]\s*(?:\$\{[\w.]*\})?\s*(/[\w\-/:{}.]+)"
)
_HTTP_METHOD_PREFIX = {
    "post": "POST", "get": "GET", "put": "PUT",
    "dele": "DELETE", "del": "DELETE", "patch": "PATCH",
}


def react_detect(repo: Path) -> bool:
    """有 React 迹象即命中: package.json 含 react 依赖, 或 repo 内存在 *.tsx。

    不局限目录名 (不写死 web-ui), 纯按内容判定。
    """
    for pkg in _iter_named(repo, "package.json"):
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        deps = {}
        for key in ("dependencies", "devDependencies"):
            section = data.get(key)
            if isinstance(section, dict):
                deps.update(section)
        if "react" in deps:
            return True
    return _has_file_with_suffix(repo, ".tsx")


def _infer_method(fn_name: str) -> str:
    low = fn_name.lower()
    for prefix, method in _HTTP_METHOD_PREFIX.items():
        if low.startswith(prefix):
            return method
    return "POST"


def scan_react(repo: Path, project_id: str) -> list[GraphNode]:
    """扫前端 API 调用 (services/apis 导出函数 + 内联 axios/fetch) + 页面组件。

    产出:
    - frontend_api_call 节点 (一个导出 api 函数 / 一处内联调用 -> url)。
    - frontend_route 节点 (调用了 api 的页面/组件文件)。
    - renders 边由调用方 (link_api_calls 之外) 不产; 页面->api 调用用 calls_api。
    """
    nodes: list[GraphNode] = []
    seen_ids: set[str] = set()

    for f in _iter_files(repo, (".ts", ".tsx")):
        if f.name in {"typings.d.ts"}:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("read fail %s: %s", f, exc)
            continue
        rel = _rel(f, repo)

        # 1) services/apis 风格: export async function xxx(...) { url: '/api/..' }
        for m in _RE_FN.finditer(text):
            fn = m.group(1)
            window = text[m.end():m.end() + 2000]
            url_m = _RE_URL.search(window)
            if not url_m:
                continue
            url = _norm_url(url_m.group(1))
            line = text.count("\n", 0, m.start()) + 1
            node_id = f"{project_id}:frontend_api_call:{rel}:{fn}"
            if node_id in seen_ids:
                continue
            seen_ids.add(node_id)
            nodes.append(
                GraphNode(
                    id=node_id,
                    kind=NodeKind.FRONTEND_API_CALL.value,
                    name=fn,
                    project_id=project_id,
                    file=rel,
                    line=line,
                    language="typescript",
                    meta={"url": url, "http_method": _infer_method(fn)},
                )
            )

        # 2) 内联 axios/fetch('/api/..') 调用 (无导出函数封装)。
        for m in _RE_INLINE_URL.finditer(text):
            url = _norm_url(m.group(1))
            line = text.count("\n", 0, m.start()) + 1
            node_id = f"{project_id}:frontend_api_call:{rel}:inline:{line}"
            if node_id in seen_ids:
                continue
            seen_ids.add(node_id)
            nodes.append(
                GraphNode(
                    id=node_id,
                    kind=NodeKind.FRONTEND_API_CALL.value,
                    name=f"{f.stem}@{line}",
                    project_id=project_id,
                    file=rel,
                    line=line,
                    language="typescript",
                    meta={"url": url, "http_method": "POST", "inline": True},
                )
            )

    return nodes


def scan_react_pages(
    repo: Path, project_id: str, api_nodes: list[GraphNode]
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """页面/组件 -> 它 import 并调用的 services/apis 函数, 产 frontend_route + calls_api 边。

    只对 services/apis 导出函数 (有具名) 建页面调用边; 内联调用本身就是页面节点。
    api 函数名 -> node id 映射用于边目标。
    """
    # 函数名 -> api node id (仅取具名导出函数节点; 内联节点名不唯一不纳入)。
    api_by_name: dict[str, str] = {}
    for n in api_nodes:
        if not n.meta.get("inline"):
            api_by_name.setdefault(n.name, n.id)

    page_nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    if not api_by_name:
        return page_nodes, edges

    imp_re = re.compile(
        r"import\s*\{([^}]*)\}\s*from\s*['\"][^'\"]*services/apis", re.S
    )
    seen_pages: set[str] = set()

    for f in _iter_files(repo, (".ts", ".tsx")):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if "services/apis" not in text:
            continue
        rel = _rel(f, repo)
        imported: set[str] = set()
        for m in imp_re.finditer(text):
            for tok in m.group(1).split(","):
                tok = tok.strip().split(" as ")[0].strip()
                if tok:
                    imported.add(tok)
        called = {
            fn for fn in imported
            if fn in api_by_name and re.search(rf"\b{re.escape(fn)}\s*\(", text)
        }
        if not called:
            continue
        page_id = f"{project_id}:frontend_route:{rel}"
        if page_id not in seen_pages:
            seen_pages.add(page_id)
            page_nodes.append(
                GraphNode(
                    id=page_id,
                    kind=NodeKind.FRONTEND_ROUTE.value,
                    name=f.parent.name if f.stem == "index" else f.stem,
                    project_id=project_id,
                    file=rel,
                    language="typescript",
                )
            )
        for fn in sorted(called):
            edges.append(
                GraphEdge(
                    source=page_id,
                    target=api_by_name[fn],
                    kind=EdgeKind.RENDERS.value,
                    meta={"evidence": f"import+call {fn}"},
                )
            )
    return page_nodes, edges
