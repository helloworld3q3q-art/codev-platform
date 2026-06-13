"""项目 → 仓根集合(主仓 + extra_repos)—— 单一真值源。

**同一逻辑项目可跨多个 git 仓**(前后端分离 / N 前端 M 后端 / PDA 前端独立仓)。"哪些仓属于该
项目"需被多处共用: ① graph ingest 把所有仓一起扫进统一图谱 ② agent 文件工具(read_file/list_dir)
让 agent 读得到关联仓的文件。两处共用本模块, 避免"多仓解析"逻辑重复/漂移。

extra_repos 两个来源合并:
- 用户 config `projects.<pid>.extra_repos`: 机器相关**绝对路径**(不进 git, 各机自给)。
- meta.json `extra_repos`: git 版本化的**可移植声明**(project-id 引用, 解析成各机 config 的 repo_path)。
  → 换机/重建 WSL 后跨仓关系不丢; 绝对路径仍由本机 config 提供(换机零改 meta)。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from codev_platform.core.config import get as _cfg_get, load_config

logger = logging.getLogger(__name__)


def resolve_meta_extra_entries(entries: list, cfg: dict) -> list[str]:
    """纯解析: meta.json extra_repos 条目 → 路径列表。每项可为已登记 project-id(解析成其
    repo_path, 可移植)或字面路径。空项跳过。无 IO → 可单测。"""
    out: list[str] = []
    for e in entries or []:
        e = str(e).strip()
        if not e:
            continue
        rp = _cfg_get(cfg, f"projects.{e}.repo_path")   # project-id ref → 该 project repo_path
        out.append(rp if rp else e)                      # 否则当字面路径
    return out


def _read_meta(project_id: str) -> dict:
    """读 platform_meta/projects/<pid>/meta.json(整份)。非 editable 安装(wheel)无 platform_meta /
    文件缺 / 损坏 → {}(优雅降级)。"""
    root = Path(__file__).resolve().parents[2]   # codev_platform/core/repos.py -> 仓根
    meta_f = root / "platform_meta" / "projects" / project_id / "meta.json"
    if not meta_f.is_file():
        return {}
    try:
        return json.loads(meta_f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def meta_extra_repos(project_id: str, cfg: dict) -> list[str]:
    """meta.json 的 extra_repos(git 版本化可移植跨仓声明)→ 解析成路径。无声明 → []。"""
    return resolve_meta_extra_entries(_read_meta(project_id).get("extra_repos") or [], cfg)


def _main_repo_root(project_id: str, cfg: dict) -> Path | None:
    """主仓根: config projects.<pid>.repo_path 优先(机器级正确), 再 fallback meta.json repo_path
    (committed, 跨平台可能不符 → is_dir 校验)。存在的目录才取, 都拿不到 → None。"""
    rp = _cfg_get(cfg, f"projects.{project_id}.repo_path")
    if rp and Path(rp).is_dir():
        return Path(rp).resolve()
    mp = _read_meta(project_id).get("repo_path")
    if mp and Path(mp).is_dir():
        return Path(mp).resolve()
    return None


def project_repo_roots(project_id: str, *, main_repo: Path | str | None = None) -> list[Path]:
    """项目的全部仓根(主仓 + extra_repos), 去重 + 仅保留**存在的目录**。

    main_repo 显式给则用之(ingest 已知主仓路径时), 否则从 config repo_path 解析。
    无 extra 声明 / 单仓项目 → 只返主仓(零影响)。
    """
    cfg = load_config()
    roots: list[Path] = []
    main = Path(main_repo).resolve() if main_repo else _main_repo_root(project_id, cfg)
    if main and main.is_dir():
        roots.append(main)
    from_cfg = _cfg_get(cfg, f"projects.{project_id}.extra_repos", []) or []
    from_meta = meta_extra_repos(project_id, cfg)
    seen = {p.resolve() for p in roots}
    for r in list(from_cfg) + [x for x in from_meta if x not in from_cfg]:
        p = Path(r).expanduser()
        # 相对路径按进程 CWD 解析 = 不确定 + 与"可移植声明"初衷相悖(应是绝对路径或已登记 project-id
        # ref)。fail-closed 丢弃: 不把服务 CWD 下偶然同名目录拉进 agent 可读白名单(项目隔离防御)。
        if not p.is_absolute():
            logger.warning("[repos] 跳过相对 extra_repos %r(须绝对路径或 project-id ref, 防 CWD 串目录)", r)
            continue
        if p.is_dir():
            rp = p.resolve()
            if rp not in seen:
                seen.add(rp)
                roots.append(rp)
    return roots
