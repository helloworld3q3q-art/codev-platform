"""栈扫描共享基座: 遍历剪枝 + 路径归一 + 通用文件探测 + 内联 url 解析。

跨栈复用的纯函数集中在此 (单一真值源): react / vue / fastapi / node / spring
各栈子模块都从这里取 _iter_files / _walk_pruned / _norm_url / _RE_INLINE_URL 等,
不各自重复实现遍历与 url 归一逻辑。
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from codev_platform.graph.schema import (
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


# 直接 axios.get('/api/..') / fetch('/api/..') / request({ url: ... }) 的裸字面量。
_RE_INLINE_URL = re.compile(
    r"""(?:axios|fetch|request)\s*(?:\.\s*(?:get|post|put|delete|patch))?\s*\(\s*[`'\"]\s*(/[\w\-/:{}.]+)"""
)


def _norm_url(url: str) -> str:
    return url.rstrip("/")


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
