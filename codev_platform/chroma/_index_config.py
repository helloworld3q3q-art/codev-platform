"""chroma 索引器 —— 共享常量 + logger 叶子模块 (file-discipline §1 + 无环分层)。

只依赖 stdlib (logging/os/pathlib)。indexer.py 与 _discover.py 都从这里取 PLATFORM_ROOT /
DOC_PATTERNS / EXCLUDE_* / logger —— 打破原来的 `indexer ↔ _discover` 双向 import 脆弱
(原 _discover 从 indexer 取常量, 直接 `import _discover` 会 circular ImportError)。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

_DEFAULT_ROOT = Path(__file__).resolve().parents[2]
PLATFORM_ROOT = Path(os.getenv("PLATFORM_ROOT", str(_DEFAULT_ROOT))).expanduser().resolve()

# DEFAULT_DOC_PATTERNS: 任何项目通用的 markdown 位置 (不含业务专属路径).
# 业务专属路径 (apps/stock-admin-* / python/stock-pipeline 等) 走各业务仓
# <repo>/.claude/index.json:doc_patterns override (见 _discover._load_project_index_config).
DOC_PATTERNS = [
    "CLAUDE.md",
    "AGENTS.md",
    "README.md",
    ".claude/rules/*.md",
    ".claude/skills/**/*.md",
    "docs/**/*.md",
]

EXCLUDE_PARTS = {"archive", "node_modules", "__pycache__", "target"}
# 白名单:即使路径含 EXCLUDE_PARTS 也保留(N10 incident 复盘需可检索)
EXCLUDE_WHITELIST_SUBPATHS = ("archive/incidents/",)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("chroma_index")
