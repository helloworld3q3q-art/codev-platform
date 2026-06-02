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
    """explicit 优先(同样走 validate 校验,防路径穿越);否则从 cwd 推导(单项目兼容)。

    Phase 0 安全底座(token 模式禁 cwd fallback): server 部署(gateway.auth_mode == "token")下,
    explicit 缺失时**不允许**回退 cwd —— 否则任何未显式带 project_id 的调用会静默命中平台进程
    cwd 推导出的项目 = 越权扫盲。HTTP /chat 路由已在入口用 can_access 挡下 token 模式 None,
    本处是 defense-in-depth: 任何直接调用工具层的路径(未来 CLI / service 直调)同样不漏。
    passthrough(dev 单机)保留 cwd 回退,单项目兼容不破。
    """
    if explicit:
        from codev_platform.core.project_id import validate
        return validate(explicit)
    from codev_platform.core.config import get as _cfg_get, load_config
    from codev_platform.core.project_id import ProjectIdError, resolve_local
    if _cfg_get(load_config(), "gateway.auth_mode", "passthrough") == "token":
        raise ProjectIdError(
            "token 模式(server 部署)禁止 cwd fallback: 必须显式提供 project_id "
            "(X-Project-Id header / body.project_id)。"
        )
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
