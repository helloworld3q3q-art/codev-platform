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
import os
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
        ".codegraph", "data", "coverage", ".mypy_cache", ".ruff_cache",
        ".idea", ".vscode", "site-packages", ".tox", ".eggs",
    }
)


def _rel(path: Path, repo: Path) -> str:
    """repo 内相对路径, 统一正斜杠 (跨平台稳定 id)。"""
    return str(path.relative_to(repo)).replace("\\", "/")


def _iter_files(repo: Path, suffixes: tuple[str, ...]) -> list[Path]:
    """递归收集指定后缀文件, 跳过构建物/依赖目录。按路径排序保证稳定。

    用 os.walk 遍历时**原地剪枝** skip 目录 + 隐藏目录 (.xxx), 根本不进入
    .venv / data / node_modules 等大目录树 (区别于 rglob 先全量下钻再过滤文件)。
    """
    out: list[Path] = []
    suffix_set = set(suffixes)
    for dirpath, _dirnames, filenames in _walk_pruned(repo):
        base = Path(dirpath)
        for name in filenames:
            if Path(name).suffix in suffix_set:
                out.append(base / name)
    return sorted(out, key=lambda p: _rel(p, repo))


def _walk_pruned(repo: Path):
    """os.walk(repo) 但**原地剪枝** skip 目录 + 隐藏目录 (.xxx), 阻断下钻。

    所有遍历入口 (_iter_files / *_detect 的文件名扫描) 共用此生成器, 保证
    .venv / data / node_modules 等大目录树根本不被进入 (rglob 做不到这点)。
    yield 与 os.walk 同形 (dirpath, dirnames, filenames)。
    """
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIRS and not d.startswith(".")
        ]
        yield dirpath, dirnames, filenames


def _iter_named(repo: Path, filename: str):
    """遍历时剪枝, 找出所有叫 <filename> 的文件 (如 package.json)。惰性 yield。"""
    for dirpath, _dirnames, filenames in _walk_pruned(repo):
        if filename in filenames:
            yield Path(dirpath) / filename


def _has_file_with_suffix(repo: Path, suffix: str) -> bool:
    """遍历时剪枝, 探测 repo 内是否存在指定后缀文件 (找到即短路, 不全量收集)。"""
    for _dirpath, _dirnames, filenames in _walk_pruned(repo):
        for name in filenames:
            if name.endswith(suffix):
                return True
    return False


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


# ============================ Vue 前端 ============================

# Vue Router 路由表条目: { path: '/foo', component: Foo } / { path: '/foo', name: 'Foo' }。
_RE_VUE_ROUTE = re.compile(
    r"""\{\s*[^{}]*?\bpath\s*:\s*[`'\"]([^`'\"]+)[`'\"][^{}]*?\}""",
    re.S,
)
_RE_VUE_ROUTE_NAME = re.compile(r"\bname\s*:\s*[`'\"]([^`'\"]+)[`'\"]")
_RE_VUE_ROUTE_COMP = re.compile(r"\bcomponent\s*:\s*([A-Za-z_$][\w$]*)")


def vue_detect(repo: Path) -> bool:
    """有 Vue 迹象即命中: package.json 含 vue 依赖, 或 repo 内存在 *.vue。

    不局限目录名, 纯按 repo 内容判定 (与 react_detect 对称, 复用 JS/TS 基座规则)。
    """
    for pkg in _iter_named(repo, "package.json"):
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        deps: dict[str, str] = {}
        for key in ("dependencies", "devDependencies"):
            section = data.get(key)
            if isinstance(section, dict):
                deps.update(section)
        if "vue" in deps:
            return True
    return _has_file_with_suffix(repo, ".vue")


def _scan_inline_api(
    text: str,
    rel: str,
    project_id: str,
    seen_ids: set[str],
    language: str,
) -> list[GraphNode]:
    """从一段文本扫内联 axios/fetch('/api/..') 调用 -> frontend_api_call 节点。

    复用 JS/TS 基座的 _RE_INLINE_URL + _norm_url (单一真值源, Vue 不另造 url 解析)。
    """
    out: list[GraphNode] = []
    for m in _RE_INLINE_URL.finditer(text):
        url = _norm_url(m.group(1))
        line = text.count("\n", 0, m.start()) + 1
        node_id = f"{project_id}:frontend_api_call:{rel}:inline:{line}"
        if node_id in seen_ids:
            continue
        seen_ids.add(node_id)
        out.append(
            GraphNode(
                id=node_id,
                kind=NodeKind.FRONTEND_API_CALL.value,
                name=f"{rel.rsplit('/', 1)[-1]}@{line}",
                project_id=project_id,
                file=rel,
                line=line,
                language=language,
                meta={"url": url, "http_method": "POST", "inline": True},
            )
        )
    return out


def scan_vue(repo: Path, project_id: str) -> list[GraphNode]:
    """扫 .vue SFC + 同仓 JS/TS 里的 axios/fetch 调用。

    产出:
    - frontend_component 节点: 每个 .vue SFC 文件一个。
    - frontend_api_call 节点: SFC <script> / 同仓 .js/.ts 内 axios/fetch('/api/..')
      内联调用, 复用 JS/TS 基座的 _RE_INLINE_URL / _norm_url。

    第一版轻量: 用正则扫 SFC + 脚本, 不追求覆盖所有写法 (template-only 组件仍产
    component 节点; setup/options API 内联请求都吃)。
    """
    nodes: list[GraphNode] = []
    seen_ids: set[str] = set()

    # 1) .vue SFC -> frontend_component + SFC 内联 api 调用。
    for f in _iter_files(repo, (".vue",)):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("read fail %s: %s", f, exc)
            continue
        rel = _rel(f, repo)
        comp_id = f"{project_id}:frontend_component:{rel}"
        if comp_id not in seen_ids:
            seen_ids.add(comp_id)
            nodes.append(
                GraphNode(
                    id=comp_id,
                    kind=NodeKind.FRONTEND_COMPONENT.value,
                    name=f.stem,
                    project_id=project_id,
                    file=rel,
                    language="vue",
                )
            )
        nodes.extend(
            _scan_inline_api(text, rel, project_id, seen_ids, language="vue")
        )

    # 2) 同仓 .js/.ts/.jsx/.tsx 内联 api 调用 (Vue 仓的 api 封装层多在脚本里)。
    for f in _iter_files(repo, (".js", ".jsx", ".ts", ".tsx")):
        if f.name == "typings.d.ts":
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = _rel(f, repo)
        nodes.extend(
            _scan_inline_api(
                text, rel, project_id, seen_ids, language="typescript"
            )
        )

    return nodes


def scan_vue_routes(
    repo: Path, project_id: str, component_nodes: list[GraphNode]
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """扫 Vue Router 路由表 -> frontend_route 节点 + renders 边 (route -> component)。

    在 .js/.ts(x) 里找 { path: '/x', component: Foo, name: 'Foo' } 形态条目。
    renders 边: route -> 对应 .vue 组件 (按 component 标识符 / name 匹配 SFC 文件名)。
    第一版轻量: 只识别 path 字面量 + 可选 component 标识符 / name, 不解析懒加载工厂体内。
    """
    # SFC 文件名 (stem) -> component node id, 用于 renders 边目标匹配。
    comp_by_stem: dict[str, str] = {}
    for n in component_nodes:
        if n.kind == NodeKind.FRONTEND_COMPONENT.value:
            comp_by_stem.setdefault(n.name, n.id)

    route_nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    seen_routes: set[str] = set()

    for f in _iter_files(repo, (".js", ".jsx", ".ts", ".tsx")):
        if f.name == "typings.d.ts":
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        # 廉价短路: 没有 path 字面量的文件直接跳过。
        if "path" not in text:
            continue
        rel = _rel(f, repo)
        for m in _RE_VUE_ROUTE.finditer(text):
            entry = m.group(0)
            path = m.group(1)
            name_m = _RE_VUE_ROUTE_NAME.search(entry)
            comp_m = _RE_VUE_ROUTE_COMP.search(entry)
            if not path.startswith("/"):
                # 非 / 前缀的对象: 仅当带 component / name 时才算路由条目。
                if not name_m and not comp_m:
                    continue
            line = text.count("\n", 0, m.start()) + 1
            route_key = path or (name_m.group(1) if name_m else f"@{line}")
            route_id = f"{project_id}:frontend_route:{rel}:{route_key}"
            if route_id in seen_routes:
                continue
            seen_routes.add(route_id)
            route_nodes.append(
                GraphNode(
                    id=route_id,
                    kind=NodeKind.FRONTEND_ROUTE.value,
                    name=name_m.group(1) if name_m else (path or f"route@{line}"),
                    project_id=project_id,
                    file=rel,
                    line=line,
                    language="typescript",
                    meta={"path": path},
                )
            )
            # renders 边: route -> SFC 组件 (按 component 标识符或 name 匹配 stem)。
            target_stem = None
            if comp_m and comp_m.group(1) in comp_by_stem:
                target_stem = comp_m.group(1)
            elif name_m and name_m.group(1) in comp_by_stem:
                target_stem = name_m.group(1)
            if target_stem:
                edges.append(
                    GraphEdge(
                        source=route_id,
                        target=comp_by_stem[target_stem],
                        kind=EdgeKind.RENDERS.value,
                        meta={"evidence": f"route->{target_stem}"},
                    )
                )

    return route_nodes, edges


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


# ============================ Node/Express 后端 ============================

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


# ============================ Spring 后端 (Java) ============================

# 方法级映射注解 -> HTTP 动词。
_SPRING_METHOD_ANN = {
    "GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT",
    "DeleteMapping": "DELETE", "PatchMapping": "PATCH",
}
# 类是否为 Controller (只在含此标记的 .java 里找端点, 避免误扫普通 @RequestMapping)。
_RE_SPRING_CONTROLLER = re.compile(r"@(?:RestController|Controller)\b")
# 任意映射注解 + 可选括号参数: @GetMapping(...) / @RequestMapping(...) / @PostMapping。
_RE_SPRING_MAPPING = re.compile(
    r"""@(?P<ann>\w*Mapping)\s*(?:\((?P<args>[^)]*)\))?""", re.S
)
# Java 关键字 (排除把它当 handler 方法名)。
_JAVA_KW = frozenset({
    "if", "for", "while", "switch", "return", "new", "catch", "synchronized",
})


def spring_detect(repo: Path) -> bool:
    """有 Spring MVC 迹象即命中: 某 .java 含 @RestController/@Controller 或 *Mapping 注解。"""
    for f in _iter_files(repo, (".java",)):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if (_RE_SPRING_CONTROLLER.search(text)
                or "Mapping" in text and _RE_SPRING_MAPPING.search(text)):
            return True
    return False


def _spring_ann_path(args: str) -> str | None:
    """从映射注解参数里取 URL path: value=/path= "..." / {"..."} / 第一个字符串字面量。"""
    m = re.search(r'(?:value|path)\s*=\s*\{?\s*"([^"]*)"', args)
    if m:
        return m.group(1)
    m = re.search(r'"([^"]*)"', args)
    return m.group(1) if m else None


def _spring_req_method(args: str) -> str:
    """@RequestMapping 的 method=RequestMethod.XXX; 未指定按项目约定降级 POST。"""
    m = re.search(r"RequestMethod\.(\w+)", args)
    return m.group(1).upper() if m else "POST"


def _join_url(base: str, path: str) -> str:
    """拼类级 base + 方法级 path, 归一斜杠 (与 _norm_url 一致)。"""
    b = (base or "").strip()
    p = (path or "").strip()
    if b and not b.startswith("/"):
        b = "/" + b
    if p and not p.startswith("/"):
        p = "/" + p
    joined = (b + p) or "/"
    return _norm_url(joined) or "/"


# 类/接口声明 (行首 + 可选修饰符), 比裸 text.find("class ") 稳健:
# 不被注释里的 "class " / getClass() / 字符串误触发。
_RE_JAVA_TYPEDECL = re.compile(
    r"(?m)^[ \t]*(?:public\s+|final\s+|abstract\s+|sealed\s+|non-sealed\s+)*"
    r"(?:class|interface|enum|record)\s+\w+",
)


def _spring_class_pos(text: str) -> int:
    """类/接口声明的起始下标 (找不到回 len, 即全文都算类级之前)。"""
    m = _RE_JAVA_TYPEDECL.search(text)
    return m.start() if m else len(text)


def _spring_class_base(text: str, class_pos: int) -> str:
    """类级 @RequestMapping base path: 取类声明之前 (head) 出现的映射 path。"""
    head = text[:class_pos]
    for m in _RE_SPRING_MAPPING.finditer(head):
        path = _spring_ann_path(m.group("args") or "")
        if path is not None:
            return path
    return ""


def _spring_handler_name(text: str, ann_end: int) -> str | None:
    """注解之后第一处方法声明的方法名 (跳过后续注解 / 修饰符)。"""
    window = text[ann_end:ann_end + 400]
    for m in re.finditer(r"([A-Za-z_]\w*)\s*\(", window):
        name = m.group(1)
        if name not in _JAVA_KW:
            return name
    return None


def scan_spring(repo: Path, project_id: str) -> list[GraphNode]:
    """正则扫 Spring MVC Controller -> backend_endpoint 节点 (language=java)。

    类级 @RequestMapping 作 base path, 方法级 @GetMapping/@PostMapping/.../@RequestMapping
    拼出完整 url。node id 与 FastAPI/Node 同构 "<pid>:backend_endpoint:<METHOD>:<url>",
    同 url 跨 java/py 可被 link_api_calls 命中。第一版轻量正则 (不解析 method 体 / 不追
    @PathVariable 模板展开), plan 允许不追求全覆盖。
    """
    nodes: list[GraphNode] = []
    seen: set[str] = set()
    for f in _iter_files(repo, (".java",)):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("read fail %s: %s", f, exc)
            continue
        if not _RE_SPRING_CONTROLLER.search(text):
            continue  # 只在 Controller 类里找端点。
        rel = _rel(f, repo)
        class_pos = _spring_class_pos(text)
        base = _spring_class_base(text, class_pos)
        for m in _RE_SPRING_MAPPING.finditer(text):
            ann = m.group("ann")
            args = m.group("args") or ""
            # 类级 @RequestMapping (声明之前) 仅作 base, 不产端点 —— 按**位置**判定,
            # 不按 path==base (避免方法级 path 恰等于 base 的真端点被误跳)。
            if ann == "RequestMapping" and m.start() < class_pos:
                continue
            if ann == "RequestMapping":
                path = _spring_ann_path(args)
                if path is None:
                    continue  # 方法级 @RequestMapping 无 path 字面量 (罕见) -> 跳过。
                method = _spring_req_method(args)
            elif ann in _SPRING_METHOD_ANN:
                method = _SPRING_METHOD_ANN[ann]
                path = _spring_ann_path(args) or ""
            else:
                continue
            url = _join_url(base, path)
            line = text.count("\n", 0, m.start()) + 1
            node_id = f"{project_id}:backend_endpoint:{method}:{url}"
            if node_id in seen:
                continue
            seen.add(node_id)
            handler = _spring_handler_name(text, m.end())
            nodes.append(
                GraphNode(
                    id=node_id,
                    kind=NodeKind.BACKEND_ENDPOINT.value,
                    name=handler or f"{method} {url}",
                    project_id=project_id,
                    file=rel,
                    line=line,
                    language="java",
                    meta={
                        "url": url,
                        "http_method": method,
                        "base_path": base,
                        "handler": handler,
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
