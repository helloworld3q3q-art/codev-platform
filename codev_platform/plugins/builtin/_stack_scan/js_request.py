"""JS/TS request-object API scanner.

Enterprise frontends often wrap HTTP calls as exported functions:

    export function query(data) {
      return request({ method: "post", url: `${prefix}/foo/query`, data })
    }

This module extracts the deterministic part of those calls without knowing any
project-specific prefix object such as ContextEnum or daoServiceClientConfig.
"""
from __future__ import annotations

import re
from pathlib import Path

from codev_platform.graph.schema import GraphNode, NodeKind

from ._common import _iter_files, _norm_url, _rel, logger

_IDENT = r"[A-Za-z_$][\w$]*"
_RE_EXPORT_FUNCTION = re.compile(rf"\bexport\s+(?:async\s+)?function\s+({_IDENT})\s*\(")
_RE_EXPORT_VALUE = re.compile(rf"\bexport\s+(?:const|let|var)\s+({_IDENT})\s*=")
_RE_CALLEE = re.compile(rf"{_IDENT}(?:\s*\.\s*{_IDENT})*")
_DYN = "\x00DYN\x00"
_HTTP_METHODS = {
    "post": "POST",
    "get": "GET",
    "put": "PUT",
    "delete": "DELETE",
    "del": "DELETE",
    "patch": "PATCH",
}
_HTTP_CALLEES = frozenset({"request", "fetch", "axios", "http"})
_PATH_RE = re.compile(r"(?<![A-Za-z0-9_.:\-])/[A-Za-z0-9][A-Za-z0-9_./:{}~%?=&+\-]*")
_REL_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.:\-])([A-Za-z][A-Za-z0-9_.\-]*/[A-Za-z0-9_./:{}~%?=&+\-]*)"
)


def infer_http_method(fn_name: str) -> str:
    low = fn_name.lower()
    for prefix, method in _HTTP_METHODS.items():
        if low.startswith(prefix):
            return method
    return "POST"


def scan_js_request_exports(
    repo: Path,
    project_id: str,
    *,
    suffixes: tuple[str, ...],
    language: str = "typescript",
) -> list[GraphNode]:
    """Scan exported JS/TS API wrappers using request({ url, method }) style."""
    nodes: list[GraphNode] = []
    seen_ids: set[str] = set()
    for f in _iter_files(repo, suffixes):
        if f.name == "typings.d.ts":
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("read fail %s: %s", f, exc)
            continue
        rel = _rel(f, repo)
        nodes.extend(scan_js_request_exports_text(
            text, rel, project_id, seen_ids, language=language))
    return nodes


def scan_js_request_exports_text(
    text: str,
    rel: str,
    project_id: str,
    seen_ids: set[str],
    *,
    language: str,
) -> list[GraphNode]:
    out: list[GraphNode] = []
    for fn, start, end in _export_ranges(text):
        body = text[start:end]
        matches = _request_calls(body)
        for idx, call in enumerate(matches):
            line = text.count("\n", 0, start + call.start) + 1
            name = fn if idx == 0 else f"{fn}@{line}"
            node_id = f"{project_id}:frontend_api_call:{rel}:{name}"
            if node_id in seen_ids:
                continue
            seen_ids.add(node_id)
            out.append(GraphNode(
                id=node_id,
                kind=NodeKind.FRONTEND_API_CALL.value,
                name=name,
                project_id=project_id,
                file=rel,
                line=line,
                language=language,
                meta={"url": call.url, "http_method": call.method},
            ))
    return out


class _RequestCall:
    def __init__(self, start: int, url: str, method: str) -> None:
        self.start = start
        self.url = url
        self.method = method


def _export_ranges(text: str) -> list[tuple[str, int, int]]:
    ranges: list[tuple[str, int, int]] = []
    function_matches = list(_RE_EXPORT_FUNCTION.finditer(text))
    value_matches = list(_RE_EXPORT_VALUE.finditer(text))
    boundaries = sorted(m.start() for m in [*function_matches, *value_matches])

    for m in function_matches:
        body_start = _function_body_start(text, m.end() - 1)
        if body_start is None:
            continue
        body_end = _find_matching(text, body_start, "{", "}")
        if body_end is not None:
            ranges.append((m.group(1), body_start + 1, body_end))
    for m in value_matches:
        end = next((b for b in boundaries if b > m.start()), len(text))
        ranges.append((m.group(1), m.end(), end))
    return sorted(ranges, key=lambda x: x[1])


def _function_body_start(text: str, paren_start: int) -> int | None:
    paren_end = _find_matching(text, paren_start, "(", ")")
    if paren_end is None:
        return None
    brace = text.find("{", paren_end)
    return brace if brace >= 0 else None


def _request_calls(text: str) -> list[_RequestCall]:
    calls: list[_RequestCall] = []
    for callee, call_start, obj_start in _iter_object_calls(text):
        obj_end = _find_matching(text, obj_start, "{", "}")
        if obj_end is None:
            continue
        obj = text[obj_start + 1:obj_end]
        url_expr = _top_level_prop(obj, "url")
        if not url_expr:
            continue
        url = _resolve_url(url_expr)
        if not url:
            continue
        method = _resolve_method(_top_level_prop(obj, "method"), callee)
        calls.append(_RequestCall(call_start, url, method))
    return calls


def _iter_object_calls(text: str):
    i = 0
    while i < len(text):
        c = text[i]
        if c in "'\"`":
            i = _skip_literal(text, i)
            continue
        if _starts_comment(text, i):
            i = _skip_comment(text, i)
            continue
        if i > 0 and re.match(r"[\w$]", text[i - 1]):
            i += 1
            continue
        m = _RE_CALLEE.match(text, i)
        if not m:
            i += 1
            continue
        callee = m.group(0)
        obj_start = _object_arg_start(text, m.end())
        if obj_start is not None and _is_http_callee(callee):
            yield callee, m.start(), obj_start
        i = m.end()


def _object_arg_start(text: str, pos: int) -> int | None:
    j = _skip_ws(text, pos)
    if j < len(text) and text[j] == "<":
        j = _skip_type_args(text, j)
        if j is None:
            return None
        j = _skip_ws(text, j)
    if j >= len(text) or text[j] != "(":
        return None
    j = _skip_ws(text, j + 1)
    return j if j < len(text) and text[j] == "{" else None


def _skip_type_args(text: str, start: int) -> int | None:
    depth = 0
    i = start
    while i < len(text):
        c = text[i]
        if c in "'\"`":
            i = _skip_literal(text, i)
            continue
        if _starts_comment(text, i):
            i = _skip_comment(text, i)
            continue
        if c == "<":
            depth += 1
        elif c == ">":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _is_http_callee(callee: str) -> bool:
    tail = callee.replace(" ", "").split(".")[-1].lower()
    return (
        tail in _HTTP_CALLEES
        or tail in _HTTP_METHODS
        or tail.endswith("request")
    )


def _resolve_method(expr: str | None, callee: str) -> str:
    if expr:
        m = re.search(r"\b(post|get|put|delete|del|patch)\b", expr, re.I)
        if m:
            return _HTTP_METHODS[m.group(1).lower()]
    tail = callee.replace(" ", "").split(".")[-1].lower()
    return _HTTP_METHODS.get(tail) or "POST"


def _resolve_url(expr: str) -> str | None:
    skeleton = _collapse_dyn(_url_skeleton(expr))
    if re.search(r"https?:\s*//", skeleton, re.I):
        return None
    candidates = list(_path_candidates(skeleton))
    if not candidates:
        return None
    if _has_dynamic_path_segment(skeleton, candidates[0][0]):
        return None
    for _start, raw, _end in candidates:
        path = raw.split("?", 1)[0].split("#", 1)[0]
        if path.endswith("/"):
            continue
        return _norm_url(path if path.startswith("/") else "/" + path)
    return None


def _path_candidates(skeleton: str):
    for m in _PATH_RE.finditer(skeleton):
        raw = m.group(0)
        if raw.startswith("//"):
            continue
        yield m.start(), raw, m.end()
    for m in _REL_PATH_RE.finditer(skeleton):
        yield m.start(1), m.group(1), m.end(1)


def _has_dynamic_path_segment(skeleton: str, first_path_start: int) -> bool:
    prefix = skeleton[:first_path_start]
    if "/" in prefix:
        return True
    path_end = len(skeleton)
    for marker in ("?", "#"):
        pos = skeleton.find(marker, first_path_start)
        if pos >= 0:
            path_end = min(path_end, pos)
    return _DYN in skeleton[first_path_start:path_end]


def _url_skeleton(expr: str) -> str:
    parts: list[str] = []
    i = 0
    while i < len(expr):
        c = expr[i]
        if c in "'\"":
            s, i = _read_string(expr, i)
            parts.append(s)
            continue
        if c == "`":
            s, i = _read_template(expr, i)
            parts.append(s)
            continue
        if c.isspace() or c in "+(),":
            i += 1
            continue
        j = i + 1
        while j < len(expr) and expr[j] not in "'\"`+,":
            j += 1
        if expr[i:j].strip():
            parts.append(_DYN)
        i = j
    return "".join(parts)


def _collapse_dyn(value: str) -> str:
    while _DYN + _DYN in value:
        value = value.replace(_DYN + _DYN, _DYN)
    return value


def _read_string(text: str, start: int) -> tuple[str, int]:
    quote = text[start]
    out: list[str] = []
    i = start + 1
    while i < len(text):
        c = text[i]
        if c == "\\":
            if i + 1 < len(text):
                out.append(text[i + 1])
            i += 2
            continue
        if c == quote:
            return "".join(out), i + 1
        out.append(c)
        i += 1
    return "".join(out), i


def _read_template(text: str, start: int) -> tuple[str, int]:
    out: list[str] = []
    i = start + 1
    while i < len(text):
        c = text[i]
        if c == "\\":
            if i + 1 < len(text):
                out.append(text[i + 1])
            i += 2
            continue
        if c == "`":
            return "".join(out), i + 1
        if c == "$" and i + 1 < len(text) and text[i + 1] == "{":
            out.append(_DYN)
            end = _find_matching(text, i + 1, "{", "}")
            i = (end + 1) if end is not None else len(text)
            continue
        out.append(c)
        i += 1
    return "".join(out), i


def _top_level_prop(obj: str, prop: str) -> str | None:
    i = 0
    depth = 0
    while i < len(obj):
        c = obj[i]
        if c in "'\"`":
            if depth == 0:
                hit = _quoted_prop(obj, i, prop)
                if hit is not None:
                    return _read_expr(obj, hit)
            i = _skip_literal(obj, i)
            continue
        if _starts_comment(obj, i):
            i = _skip_comment(obj, i)
            continue
        if c in "{[(":
            depth += 1
        elif c in "}])" and depth > 0:
            depth -= 1
        elif depth == 0:
            hit = _identifier_prop(obj, i, prop)
            if hit is not None:
                return _read_expr(obj, hit)
        i += 1
    return None


def _quoted_prop(text: str, start: int, prop: str) -> int | None:
    value, end = _read_string(text, start)
    if value != prop:
        return None
    j = _skip_ws(text, end)
    return j + 1 if j < len(text) and text[j] == ":" else None


def _identifier_prop(text: str, start: int, prop: str) -> int | None:
    if not text.startswith(prop, start):
        return None
    before = text[start - 1] if start > 0 else ""
    after_pos = start + len(prop)
    after = text[after_pos] if after_pos < len(text) else ""
    if (before and re.match(r"[\w$]", before)) or (after and re.match(r"[\w$]", after)):
        return None
    j = _skip_ws(text, after_pos)
    return j + 1 if j < len(text) and text[j] == ":" else None


def _read_expr(text: str, start: int) -> str:
    i = start
    depth = 0
    while i < len(text):
        c = text[i]
        if c in "'\"`":
            i = _skip_literal(text, i)
            continue
        if _starts_comment(text, i):
            i = _skip_comment(text, i)
            continue
        if c in "{[(":
            depth += 1
        elif c in "}])" and depth > 0:
            depth -= 1
        elif c == "," and depth == 0:
            break
        i += 1
    return text[start:i].strip()


def _find_matching(text: str, start: int, open_ch: str, close_ch: str) -> int | None:
    depth = 0
    i = start
    while i < len(text):
        c = text[i]
        if c in "'\"`":
            i = _skip_literal(text, i)
            continue
        if _starts_comment(text, i):
            i = _skip_comment(text, i)
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _skip_literal(text: str, start: int) -> int:
    if text[start] == "`":
        _, end = _read_template(text, start)
        return end
    _, end = _read_string(text, start)
    return end


def _starts_comment(text: str, i: int) -> bool:
    return i + 1 < len(text) and text[i] == "/" and text[i + 1] in "/*"


def _skip_comment(text: str, i: int) -> int:
    if text[i + 1] == "/":
        end = text.find("\n", i + 2)
        return len(text) if end < 0 else end + 1
    end = text.find("*/", i + 2)
    return len(text) if end < 0 else end + 2


def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i].isspace():
        i += 1
    return i
