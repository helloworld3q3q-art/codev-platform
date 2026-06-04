"""Spring 后端栈扫描 (Java): detect + 正则扫 Controller -> backend_endpoint。"""
from __future__ import annotations

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
