"""代码向量的文本构造、源码切块与 manifest 差异计算。"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path


logger = logging.getLogger(__name__)

_TEXT_FIELDS = ("name", "qualifiedName", "signature", "docstring")
_BASE_FIELD_MAX = {"signature": 600, "docstring": 1200}
_SNIPPET_MAX_CHARS = 1500

_CONTAINER_KINDS = frozenset(
    {"class", "interface", "enum", "struct", "trait", "module", "namespace"},
)
_BODY_KINDS = frozenset({"method", "function", "constructor", "component"})
_CHUNK_BODY_CHARS = 3500
_CHUNK_OVERLAP_LINES = 8
_CLASS_HEAD_CHARS = 1800
_MAX_CHUNKS_PER_NODE = 12


def build_text(node: dict) -> str:
    """把 codegraph 节点转换为有长度上界的基础嵌入文本。"""
    parts: list[str] = []
    for field in _TEXT_FIELDS:
        value = node.get(field)
        if not value:
            continue
        text = str(value)
        cap = _BASE_FIELD_MAX.get(field)
        parts.append(text[:cap] if cap and len(text) > cap else text)
    return "\n".join(parts)


def _source_snippet(repo, node: dict, max_chars: int = _SNIPPET_MAX_CHARS) -> str:
    """读取节点源码片段；路径或行号无效时降级为空字符串。"""
    lines = _node_source_lines(repo, node)
    return "\n".join(lines)[:max_chars]


def _embed_text(node: dict, repo) -> str:
    """兼容旧调用：拼接基础文本与单段源码片段。"""
    base = build_text(node)
    snippet = _source_snippet(repo, node)
    return f"{base}\n{snippet}" if snippet else base


def _node_source_lines(repo, node: dict) -> list[str]:
    """读取节点对应的源码行，并阻止绝对路径与父目录穿越。"""
    if repo is None:
        return []
    file_path = node.get("filePath")
    start_line = node.get("startLine")
    end_line = node.get("endLine")
    if not file_path or not start_line:
        return []
    try:
        root = Path(repo).resolve()
        path = (root / file_path).resolve()
        if not path.is_relative_to(root):
            logger.warning("[code_vec] 跳过越界 filePath(疑似路径穿越): %r", file_path)
            return []
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return lines[max(0, int(start_line) - 1) : int(end_line or start_line)]
    except Exception:  # noqa: BLE001 -- 源码富化失败不影响其它字段
        return []


def _head_at_line_boundary(lines: list[str], budget: int) -> str:
    """按完整行截取头部；即使首行超限也至少返回一行。"""
    selected: list[str] = []
    length = 0
    for line in lines:
        if selected and length + len(line) + 1 > budget:
            break
        selected.append(line)
        length += len(line) + 1
    return "\n".join(selected)


def _window_lines(lines: list[str], budget: int, overlap: int) -> list[str]:
    """按行边界生成带重叠的滑动窗口。"""
    windows: list[str] = []
    index, total = 0, len(lines)
    while index < total:
        current: list[str] = []
        length, cursor = 0, index
        while cursor < total:
            line = lines[cursor]
            if current and length + len(line) + 1 > budget:
                break
            if not current and len(line) + 1 > budget:
                current.append(line[:budget])
                cursor += 1
                break
            current.append(line)
            length += len(line) + 1
            cursor += 1
        windows.append("\n".join(current))
        if cursor >= total:
            break
        index = max(cursor - overlap, index + 1)
    return windows


def _node_chunks(node: dict, repo) -> list[tuple[str, str]]:
    """按节点种类生成一个或多个嵌入文本块。"""
    node_id = str(node.get("id"))
    base = build_text(node)
    kind = node.get("kind") or ""
    lines = _node_source_lines(repo, node)

    if kind in _CONTAINER_KINDS:
        head = _head_at_line_boundary(lines, _CLASS_HEAD_CHARS)
        text = f"{base}\n{head}" if head else base
        return [(node_id, text)] if text.strip() else []

    if kind in _BODY_KINDS and lines and len("\n".join(lines)) > _CHUNK_BODY_CHARS:
        windows = _window_lines(
            lines,
            _CHUNK_BODY_CHARS,
            _CHUNK_OVERLAP_LINES,
        )[:_MAX_CHUNKS_PER_NODE]
        chunks = [
            (f"{node_id}#{index}", f"{base}\n{window}")
            for index, window in enumerate(windows)
            if (base + window).strip()
        ]
        if chunks:
            return chunks
        return [(node_id, base)] if base.strip() else []

    head = _head_at_line_boundary(lines, _CHUNK_BODY_CHARS)
    text = f"{base}\n{head}" if head else base
    return [(node_id, text)] if text.strip() else []


def _node_id_of(chunk_id: str) -> str:
    """把子块 ID 还原为 codegraph 节点 ID。"""
    return chunk_id.split("#", 1)[0]


def _node_hash(text: str) -> str:
    """返回嵌入文本的稳定 SHA-1 摘要。"""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _diff_manifest(old: dict, new: dict) -> tuple[list[str], list[str]]:
    """计算新增或变更 ID 与已删除 ID。"""
    changed = [node_id for node_id, digest in new.items() if old.get(node_id) != digest]
    deleted = [node_id for node_id in old if node_id not in new]
    return changed, deleted
