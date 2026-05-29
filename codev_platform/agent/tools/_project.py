"""工具层的 project 上下文解析(P2 多租户)。

约定:工具构造时拿到一个 project_id(可能为 None)。
- None  → 单项目兼容:从进程 cwd 推导(resolve_local / cwd 上溯),与改造前行为一致。
- 具体值 → 多租户:按 project_id 路由到对应数据(cross_link/chroma 走平台 data 目录;
  codegraph 走该项目仓的 .codegraph)。
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def resolve_project_id(explicit: str | None) -> str:
    """explicit 优先;否则从 cwd 推导(单项目兼容)。"""
    if explicit:
        return explicit
    from codev_platform.core.project_id import resolve_local
    return resolve_local()


def _platform_meta_dir() -> Path | None:
    """platform_meta/projects 目录。env 覆盖 > 相对 codev_platform 包(editable 安装即仓内)。"""
    env = os.environ.get("PLATFORM_META_DIR")
    if env:
        return Path(env).expanduser()
    # codegraph.py 在 codev_platform/agent/tools/ → parents[3] = 仓根(editable)
    import codev_platform
    repo = Path(codev_platform.__file__).resolve().parent.parent  # <repo>/codev_platform → <repo>
    cand = repo / "platform_meta" / "projects"
    return cand if cand.is_dir() else None


def repo_path_of(project_id: str) -> Path | None:
    """按 project_id 读 meta.json 的 repo_path(codegraph per-repo db 定位用)。找不到返 None。"""
    meta_dir = _platform_meta_dir()
    if meta_dir is None:
        return None
    meta = meta_dir / project_id / "meta.json"
    try:
        raw = meta.read_text(encoding="utf-8")
        data = json.loads(raw)
        rp = data.get("repo_path")
        return Path(rp) if isinstance(rp, str) and rp else None
    except (OSError, json.JSONDecodeError):
        return None
