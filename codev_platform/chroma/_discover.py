"""chroma 索引器 —— 文件发现 + 分类 (从 indexer.py 抽出, file-discipline §1)。

discover_files / infer_category / infer_module / _rel_path 等。常量 (PLATFORM_ROOT /
DOC_PATTERNS / EXCLUDE_*) + logger 从 **叶子 _index_config** 取 (不依赖 indexer), 故
`import _discover` 可独立成功, 无 indexer↔_discover 循环 (审计 LOW#1 修复)。
"""
from __future__ import annotations

import json
from pathlib import Path

from codev_platform.chroma._index_config import (
    PLATFORM_ROOT,
    DOC_PATTERNS,
    EXCLUDE_PARTS,
    EXCLUDE_WHITELIST_SUBPATHS,
    logger,
)


# ---- 文件发现 ----

def is_excluded(path: Path) -> bool:
    """命中 archive / node_modules / __pycache__ / target 任一目录则排除;
    archive/incidents/ 白名单豁免(N10:复盘文档需可检索)"""
    rel = str(path).replace("\\", "/")
    for whitelist in EXCLUDE_WHITELIST_SUBPATHS:
        if whitelist in rel:
            return False
    return any(part in EXCLUDE_PARTS for part in path.parts)


def _load_project_index_config() -> tuple[list[str], list[str]]:
    """读 <PLATFORM_ROOT>/.claude/index.json (若存在), 返回 (doc_patterns, external_doc_paths).

    - doc_patterns: 相对 PLATFORM_ROOT 的 glob (默认走 DOC_PATTERNS hardcode 列表)
    - external_doc_paths: 跨仓真值源 glob, 支持相对路径 (基于 PLATFORM_ROOT) 或绝对路径
      例(相对, 推荐): "../codev-platform/rules/*.md"  → 跨机器 portable
      例(绝对, 兼容): "D:/WorkSpace/codev-platform/rules/*.md"  → 机器绑定 (违反 feedback_no_absolute_paths)
    """
    cfg = PLATFORM_ROOT / ".claude" / "index.json"
    patterns: list[str] = list(DOC_PATTERNS)
    external: list[str] = []
    if not cfg.is_file():
        return patterns, external
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
        if isinstance(data.get("doc_patterns"), list) and data["doc_patterns"]:
            patterns = list(data["doc_patterns"])
            logger.info("loaded %d doc_patterns from %s (override default %d)",
                        len(patterns), cfg, len(DOC_PATTERNS))
        if isinstance(data.get("external_doc_paths"), list):
            external = [str(p) for p in data["external_doc_paths"]]
            if external:
                logger.info("loaded %d external_doc_paths from %s", len(external), cfg)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("failed to parse %s: %s, fallback to default", cfg, exc)
    return patterns, external


def discover_files() -> list[Path]:
    """按 DOC_PATTERNS glob 全平台 markdown，去重 + 排除黑名单, 含 external_doc_paths 跨仓真值源."""
    import glob as _glob
    patterns, external = _load_project_index_config()
    seen: set[Path] = set()
    # 本仓 glob (相对 PLATFORM_ROOT)
    for pattern in patterns:
        for p in PLATFORM_ROOT.glob(pattern):
            if not p.is_file():
                continue
            if is_excluded(p.relative_to(PLATFORM_ROOT)):
                continue
            seen.add(p.resolve())
    # 外部 glob (cross-repo 真值源). 相对路径 anchor 到 PLATFORM_ROOT, 绝对路径直用.
    for ext_pattern in external:
        if Path(ext_pattern).is_absolute():
            anchored = ext_pattern
        else:
            anchored = str(PLATFORM_ROOT / ext_pattern)
        for s in _glob.glob(anchored, recursive=True):
            p = Path(s)
            if p.is_file():
                seen.add(p.resolve())
    return sorted(seen)


# ---- metadata 推断 ----

def infer_category(path: str) -> str:
    """路径到分类的映射"""
    p = path.replace("\\", "/")
    # memory 用户偏好优先级最高(2026-05-26),内容紧凑直接召回
    # 注意:相对路径 "docs/memory/xxx.md" 没前导 "/",不能写 "/docs/memory/" in p
    if "docs/memory/" in p:
        return "memory"
    # 工具栈开发流程演化 — 两种布局都认:
    #   业务仓: docs/dev-evolution/{log,incidents,plans}
    #   codev-platform 仓: 顶层 docs/{log,incidents,plans}(plans/roadmap-* 也属 dev_log,
    #   必须早于下方 roadmap->design 规则,否则会被误判成业务 design)
    if "docs/dev-evolution/incidents/" in p or "docs/incidents/" in p:
        return "tooling_incident"
    if "docs/dev-evolution/" in p or "docs/log/" in p or "docs/plans/" in p:
        return "dev_log"
    if "/rules/" in p:
        return "rule"
    # incident 优先级高于 operations(N10):incident-*.md / archive/incidents/ / daily-summary
    if "/archive/incidents/" in p or "/incident-" in p or "daily-summary" in p:
        return "incident"
    if "roadmap" in p or "/architecture/" in p:
        return "design"
    if "/operations/" in p:
        return "operations"
    if "/skills/" in p:
        return "skill"
    if p.endswith("CLAUDE.md") or p.endswith("AGENTS.md"):
        return "claude_md"
    if p.startswith("tools/"):
        return "tool_doc"
    return "doc"


def infer_module(path: str) -> str:
    """子模块归属"""
    p = path.replace("\\", "/")
    if p.startswith("apps/stock-admin-api/"):
        return "stock-admin-api"
    if p.startswith("apps/stock-admin-web/"):
        return "stock-admin-web"
    if p.startswith("python/stock-pipeline/"):
        return "stock-pipeline"
    return "platform"


def _rel_path(f: Path) -> str:
    """文件路径的稳定标识. 本仓内 -> relative_to(PLATFORM_ROOT); 跨仓 external -> 绝对路径 (as posix)."""
    try:
        return f.relative_to(PLATFORM_ROOT).as_posix()
    except ValueError:
        return f.as_posix()
