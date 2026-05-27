"""三级切分 chunk 策略 (markdown -> chunks).

抽自 platform/tools/chroma/index_docs.py 通用部分。算法对任何 markdown 文档通用,
不绑业务。配置 (CHUNK_TARGET_MAX / CHUNK_HARD_MAX) 是平台级默认, 可被调用方覆盖。

策略 (3 级):
1. heading: 按 H1/H2/H3 切段, 标题保留在段开头
2. paragraph: 段超 CHUNK_TARGET_MAX 再按段落 (空行分隔) 细切, 贪心合并到目标区间
3. hard: 单段仍超 CHUNK_HARD_MAX -> 字符强切

调用方仅需 chunk_text(markdown_text) -> list[str]。

2026-05-23 bug 修复: 原 <= CHUNK_TARGET_MAX 条件让 1000-2000 字段段直接跳过二/三级切分,
导致 41 个 chunks 超 HARD_MAX=1500. 改 <= HARD_MAX (1500 内直接收), 大于才进二级.
"""
from __future__ import annotations

import re
from pathlib import Path

# 加大 chunk (2026-05-23): 有 Reranker 精排后, 大 chunk 给 reranker 更完整上下文判断,
# 规则文件 "反例 + PR 自检" 等结构化段落不再被切碎.
CHUNK_TARGET_MAX = 1000
CHUNK_HARD_MAX = 1500

_HEADING_RE = re.compile(r"^(#{1,3})\s+", re.MULTILINE)


def split_by_heading(text: str) -> list[str]:
    """按 H1/H2/H3 切段（标题保留在段开头）"""
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [text]
    sections: list[str] = []
    if matches[0].start() > 0:
        head = text[: matches[0].start()].strip()
        if head:
            sections.append(head)
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        seg = text[start:end].strip()
        if seg:
            sections.append(seg)
    return sections


def split_by_paragraph(text: str) -> list[str]:
    """按空行切段"""
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def hard_split(text: str, size: int = CHUNK_HARD_MAX) -> list[str]:
    """强制按字符切, 最后兜底"""
    return [text[i : i + size] for i in range(0, len(text), size)]


def chunk_text(text: str, target_max: int = CHUNK_TARGET_MAX, hard_max: int = CHUNK_HARD_MAX) -> list[str]:
    """三级 chunk 策略 (heading -> paragraph greedy merge -> hard split)。"""
    out: list[str] = []
    for sec in split_by_heading(text):
        if len(sec) <= hard_max:
            out.append(sec)
            continue
        # 二级: 段落
        buf = ""
        for para in split_by_paragraph(sec):
            if len(para) > hard_max:
                if buf:
                    out.append(buf)
                    buf = ""
                out.extend(hard_split(para, size=hard_max))
                continue
            if not buf:
                buf = para
            elif len(buf) + len(para) + 2 <= target_max:
                buf = buf + "\n\n" + para
            else:
                out.append(buf)
                buf = para
        if buf:
            out.append(buf)
    return [c for c in (s.strip() for s in out) if c]


def file_sha256(path: Path) -> str:
    """文件 SHA256 (用于 manifest 增量探测)."""
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as f:
        for blk in iter(lambda: f.read(65536), b""):
            h.update(blk)
    return h.hexdigest()
