"""Audit: 检查 docs/ 顶级所有子目录都被 DOC_PATTERNS 白名单覆盖

防止 silent failure:加了新顶级 docs 子目录(如 docs/plans/),
但忘了同步加 DOC_PATTERNS,导致文件不被索引,search_docs 返回空 / 0 chunks。

跑法:
    tools/chroma/.venv/Scripts/python.exe tools/chroma/audit_doc_patterns_coverage.py

退出码:
    0  全部覆盖
    1  有 docs/ 子目录不被任何 pattern 匹配

加到 pre-push gate(audit 6/6),失败直接拒绝 push,转 silent failure 为 loud。

例外白名单(预期不索引,不算违规):
    docs/api/swagger 等自动生成 / cache 目录可以加进 _EXEMPT_SUBDIRS
"""

from __future__ import annotations

import sys
from pathlib import Path

# indexer 在同 package 内 (codev_platform.chroma.indexer)
from codev_platform.chroma.indexer import DOC_PATTERNS, PLATFORM_ROOT  # noqa: E402

# 顶级 docs 子目录例外白名单(预期不索引)
_EXEMPT_SUBDIRS: set[str] = set()


def find_uncovered_docs_subdirs() -> list[str]:
    """返回 docs/ 下没被 DOC_PATTERNS 任何 pattern 匹配的顶级子目录列表(相对路径)。"""
    docs_root = PLATFORM_ROOT / "docs"
    if not docs_root.exists():
        return []

    uncovered: list[str] = []
    for sub in sorted(docs_root.iterdir()):
        if not sub.is_dir():
            continue
        if sub.name in _EXEMPT_SUBDIRS:
            continue
        # 该子目录下任意 .md 是否被任一 pattern 匹配?
        md_files = list(sub.rglob("*.md"))
        if not md_files:
            continue  # 空目录跳过
        sample = md_files[0]
        rel_sample = sample.relative_to(PLATFORM_ROOT).as_posix()
        covered = False
        for pattern in DOC_PATTERNS:
            for match in PLATFORM_ROOT.glob(pattern):
                if match.resolve() == sample.resolve():
                    covered = True
                    break
            if covered:
                break
        if not covered:
            uncovered.append(f"docs/{sub.name}")
    return uncovered


def main() -> int:
    uncovered = find_uncovered_docs_subdirs()
    if not uncovered:
        print("[OK] docs/ 所有顶级子目录都被 DOC_PATTERNS 覆盖")
        return 0
    print("[FAIL] 以下 docs/ 顶级子目录未被 DOC_PATTERNS 覆盖,加新 pattern 或加入 _EXEMPT_SUBDIRS:")
    for d in uncovered:
        print(f"  - {d}/")
    print()
    print("修复:在 tools/chroma/index_docs.py 的 DOC_PATTERNS 列表加入对应 pattern,例如:")
    for d in uncovered:
        print(f'  "{d}/**/*.md",')
    return 1


if __name__ == "__main__":
    sys.exit(main())
