"""Vue 前端栈扫描: detect + .vue SFC 组件 + 内联 api 调用 + Vue Router 路由表。"""
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
    _has_file_with_suffix,
    _iter_files,
    _iter_named,
    _scan_inline_api,
    _rel,
    logger,
)

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
