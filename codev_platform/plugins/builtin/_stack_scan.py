"""通用栈插件共享扫描工具 (FrontendReact / FastApi 复用)。

把 tools/cross_link/build_codev.py 里一次性、绑死 codev-platform 目录布局的扫描思路
抽成**与项目无关**的纯函数: 给定 repo 根, 探测技术栈 + 扫出 frontend/backend 对象,
产出统一 graph.schema 的 GraphNode/GraphEdge。任意 React / FastAPI 仓都能复用,
不再 per-project 写脚本。

设计要点:
- detect 基于 **repo 内容** (package.json deps / *.tsx 存在 / import fastapi / @router 装饰器),
  不基于项目名或固定目录名 (web-ui / codev_platform 都不写死)。
- node id 统一 "<project_id>:<kind>:<stable-key>" (与 graph/adapters/cross_link 同构,
  保证跨插件可链接)。
- URL 匹配建 calls_api 边的逻辑集中在 link_api_calls() 一处 (单一真值源)。
- 不吞异常返回空: 单文件 parse / read 失败记 warning 并跳过该文件, 不中断整体。
"""
from __future__ import annotations

import ast
import json
import logging
import re
from pathlib import Path

from codev_platform.graph.schema import (
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)

logger = logging.getLogger(__name__)

# 默认不下钻的目录 (构建物 / 依赖 / 缓存), 任意栈通用。
_SKIP_DIRS = frozenset(
    {
        "node_modules", ".git", "dist", ".umi", ".umi-production", "target",
        "__pycache__", ".pytest_cache", ".venv", "venv", "build", ".next",
        ".codegraph", "data",
    }
)


def _rel(path: Path, repo: Path) -> str:
    """repo 内相对路径, 统一正斜杠 (跨平台稳定 id)。"""
    return str(path.relative_to(repo)).replace("\\", "/")


def _iter_files(repo: Path, suffixes: tuple[str, ...]) -> list[Path]:
    """递归收集指定后缀文件, 跳过构建物/依赖目录。按路径排序保证稳定。"""
    out: list[Path] = []
    for path in repo.rglob("*"):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        parts = set(path.parts)
        if parts & _SKIP_DIRS:
            continue
        out.append(path)
    return sorted(out, key=lambda p: _rel(p, repo))


# ============================ React 前端 ============================

# axios / fetch / 项目 fetch 封装 / 生成的 services 里的 url 字面量。
_RE_FN = re.compile(r"export\s+async\s+function\s+(\w+)\s*\(")
_RE_URL = re.compile(
    r"url:\s*[`'\"]\s*(?:\$\{[\w.]*\})?\s*(/[\w\-/:{}.]+)"
)
# 直接 axios.get('/api/..') / fetch('/api/..') / request({ url: ... }) 的裸字面量。
_RE_INLINE_URL = re.compile(
    r"""(?:axios|fetch|request)\s*(?:\.\s*(?:get|post|put|delete|patch))?\s*\(\s*[`'\"]\s*(/[\w\-/:{}.]+)"""
)
_HTTP_METHOD_PREFIX = {
    "post": "POST", "get": "GET", "put": "PUT",
    "dele": "DELETE", "del": "DELETE", "patch": "PATCH",
}


def react_detect(repo: Path) -> bool:
    """有 React 迹象即命中: package.json 含 react 依赖, 或 repo 内存在 *.tsx。

    不局限目录名 (不写死 web-ui), 纯按内容判定。
    """
    for pkg in repo.rglob("package.json"):
        if set(pkg.parts) & _SKIP_DIRS:
            continue
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
    for tsx in repo.rglob("*.tsx"):
        if set(tsx.parts) & _SKIP_DIRS:
            continue
        return True
    return False


def _infer_method(fn_name: str) -> str:
    low = fn_name.lower()
    for prefix, method in _HTTP_METHOD_PREFIX.items():
        if low.startswith(prefix):
            return method
    return "POST"


def _norm_url(url: str) -> str:
    return url.rstrip("/")


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


# ============================ FastAPI 后端 ============================

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


# ============================ 跨插件链接 ============================


def link_api_calls(
    frontend_api_nodes: list[GraphNode],
    backend_endpoint_nodes: list[GraphNode],
) -> list[GraphEdge]:
    """按 URL (+ method 容差) 匹配 frontend_api_call -> backend_endpoint, 建 calls_api 边。

    精确 url+method 命中 confidence=1.0; url 命中但 method 不一致 confidence=0.7
    (仍建边便于发现, evidence 标注)。单一真值源: 任何栈插件组合都调本函数。
    """
    by_url: dict[str, list[tuple[str, str]]] = {}
    for n in backend_endpoint_nodes:
        url = str(n.meta.get("url") or "").strip()
        if not url:
            continue
        method = str(n.meta.get("http_method") or "POST").upper()
        by_url.setdefault(url, []).append((n.id, method))

    edges: list[GraphEdge] = []
    for n in frontend_api_nodes:
        url = str(n.meta.get("url") or "").strip()
        if not url:
            continue
        method = str(n.meta.get("http_method") or "POST").upper()
        candidates = by_url.get(url)
        if not candidates:
            continue
        chosen = None
        for ep_id, ep_method in candidates:
            if ep_method == method:
                chosen = (ep_id, 1.0, "exact")
                break
        if chosen is None:
            ep_id, ep_method = candidates[0]
            chosen = (ep_id, 0.7, f"method_mismatch front={method} back={ep_method}")
        ep_id, conf, tag = chosen
        edges.append(
            GraphEdge(
                source=n.id,
                target=ep_id,
                kind=EdgeKind.CALLS_API.value,
                confidence=conf,
                meta={"evidence": f"{tag} url={url}"},
            )
        )
    return edges
