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

from dataclasses import dataclass
import json
import logging
from pathlib import Path

from codev_platform.core.config import get as _cfg_get, load_config

logger = logging.getLogger(__name__)

_REPO_TAG_SEP = "::"


@dataclass(frozen=True)
class RepoSpec:
    """一个逻辑项目下的一座代码仓。

    主仓 tag 固定为空字符串, 让既有单仓 ref/file 保持不变。extra 仓用稳定 tag
    做命名空间, 只在跨仓可能碰撞的索引/召回 ref 上加前缀。
    """

    root: Path
    tag: str = ""
    is_main: bool = False
    source_project_id: str | None = None

    @property
    def codegraph_db(self) -> Path:
        """该仓本地 codegraph sqlite。若 .codegraph 是 junction/symlink, 路径仍透明可读。"""
        return self.root / ".codegraph" / "codegraph.db"

    def local_ref(self, ref: str) -> str:
        """仓内 codegraph node id → 逻辑项目全局 ref。主仓不改, extra 仓加 tag。"""
        return f"{self.tag}{_REPO_TAG_SEP}{ref}" if self.tag else ref

    def local_file(self, file: str | None) -> str | None:
        """仓内相对文件路径 → 逻辑项目全局文件标识。主仓不改, extra 仓加 tag。"""
        if not file:
            return file
        return f"{self.tag}{_REPO_TAG_SEP}{file}" if self.tag else file


def split_repo_tag(value: str) -> tuple[str | None, str]:
    """把 `tag::ref/path` 拆成 (tag, local)。无 tag 返回 (None, 原值)。"""
    tag, sep, local = str(value or "").partition(_REPO_TAG_SEP)
    if not sep:
        return None, str(value or "")
    return tag, local


def resolve_tagged_value(specs: list[RepoSpec], value: str) -> tuple[RepoSpec | None, str]:
    """按 RepoSpec 反解 `tag::ref/path`。无 tag → (None, 原值);未知 tag → (None, 原值)。"""
    tag, local = split_repo_tag(value)
    if tag is None:
        return None, local
    for spec in specs:
        if spec.tag == tag:
            return spec, local
    return None, value


def _stable_tags(repos: list[Path]) -> list[str]:
    """主仓 tag='', extra 仓 tag=basename, basename 撞时缀序号。"""
    tags: list[str] = []
    used: set[str] = set()
    for i, repo in enumerate(repos):
        if i == 0:
            tags.append("")
            used.add("")
            continue
        base = repo.name or f"repo{i}"
        tag = base
        n = 1
        while tag in used:
            n += 1
            tag = f"{base}-{n}"
        tags.append(tag)
        used.add(tag)
    return tags


def repo_specs_from_roots(roots: list[Path], *,
                          source_ids: list[str | None] | None = None) -> list[RepoSpec]:
    """已解析仓根 → RepoSpec。供只拿到 roots 的旧调用复用同一 tag 规则。"""
    tags = _stable_tags(roots)
    source_ids = source_ids or [None] * len(roots)
    return [
        RepoSpec(root=root, tag=tag, is_main=(i == 0), source_project_id=source_ids[i])
        for i, (root, tag) in enumerate(zip(roots, tags))
    ]


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


def _known_project_ids(cfg: dict) -> list[str]:
    """本机可见 project_id: config 登记 + platform_meta 声明。"""
    ids: list[str] = []

    def add(pid: str) -> None:
        pid = str(pid).strip()
        if pid and pid not in ids:
            ids.append(pid)

    projects = _cfg_get(cfg, "projects") or {}
    if isinstance(projects, dict):
        for pid in projects:
            add(pid)

    root = Path(__file__).resolve().parents[2]
    meta_root = root / "platform_meta" / "projects"
    try:
        for p in meta_root.iterdir():
            if p.is_dir() and (p / "meta.json").is_file():
                add(p.name)
    except OSError:
        pass
    return ids


def _raw_extra_refs(project_id: str, cfg: dict) -> list[str]:
    """不解析路径的 extra_repos 原始条目, 用于 project-id 反向依赖。"""
    refs: list[str] = []
    for entries in (
        _cfg_get(cfg, f"projects.{project_id}.extra_repos", []) or [],
        _read_meta(project_id).get("extra_repos") or [],
    ):
        for e in entries or []:
            raw = str(e).strip()
            if raw and raw not in refs:
                refs.append(raw)
    return refs


def meta_extra_repos(project_id: str, cfg: dict) -> list[str]:
    """meta.json 的 extra_repos(git 版本化可移植跨仓声明)→ 解析成路径。无声明 → []。"""
    return resolve_meta_extra_entries(_read_meta(project_id).get("extra_repos") or [], cfg)


def _resolve_extra_entries_with_source(entries: list, cfg: dict) -> list[tuple[str, str | None]]:
    """extra_repos 条目 → (path_or_raw, source_project_id)。无 IO, 保留 project-id 来源。"""
    out: list[tuple[str, str | None]] = []
    for e in entries or []:
        raw = str(e).strip()
        if not raw:
            continue
        rp = _cfg_get(cfg, f"projects.{raw}.repo_path")
        out.append((rp, raw) if rp else (raw, None))
    return out


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
    return [s.root for s in project_repo_specs(project_id, main_repo=main_repo, cfg=cfg)]


def project_repo_specs(project_id: str, *, main_repo: Path | str | None = None,
                       cfg: dict | None = None) -> list[RepoSpec]:
    """项目的全部仓描述(主仓 + extra_repos), 去重 + 稳定 tag。

    这是多仓 codegraph/code_vec/recall 的中性真值源。旧调用只需要 Path 时继续用
    project_repo_roots()。
    """
    cfg = load_config() if cfg is None else cfg
    roots: list[Path] = []
    source_ids: list[str | None] = []
    main = Path(main_repo).resolve() if main_repo else _main_repo_root(project_id, cfg)
    if main and main.is_dir():
        roots.append(main)
        source_ids.append(project_id)

    from_cfg = _resolve_extra_entries_with_source(
        _cfg_get(cfg, f"projects.{project_id}.extra_repos", []) or [], cfg)
    from_meta = _resolve_extra_entries_with_source(_read_meta(project_id).get("extra_repos") or [], cfg)

    seen = {p.resolve() for p in roots}
    seen_entries = {path for path, _ in from_cfg}
    for r, source_pid in list(from_cfg) + [x for x in from_meta if x[0] not in seen_entries]:
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
                source_ids.append(source_pid)

    return repo_specs_from_roots(roots, source_ids=source_ids)


def impacted_project_ids_for_repo(repo: Path | str | None, *,
                                  primary_project_id: str | None = None,
                                  cfg: dict | None = None) -> list[str]:
    """给定发生变更的仓, 返回需要刷新的逻辑项目。

    用于 post-commit/webhook 入队边界: extra repo 自己变更时, 除了刷新它自身, 还要刷新把它
    挂为 extra_repos 的父项目。只返回 project_id, 不碰队列/runner。
    """
    cfg = load_config() if cfg is None else cfg
    known = _known_project_ids(cfg)
    out: list[str] = []

    def add(pid: str | None) -> None:
        pid = str(pid or "").strip()
        if pid and pid not in out:
            out.append(pid)

    add(primary_project_id)

    repo_root: Path | None = None
    if repo is not None:
        try:
            repo_root = Path(repo).expanduser().resolve()
        except OSError:
            repo_root = None

    if repo_root is not None:
        for pid in known:
            try:
                specs = project_repo_specs(pid, cfg=cfg)
            except Exception as exc:  # noqa: BLE001 - 反查失败不应阻断 hook/webhook
                logger.warning("[repos] 反查项目 %s 的 repo specs 失败: %s", pid, exc)
                continue
            if any(spec.root.resolve() == repo_root for spec in specs):
                add(pid)

    i = 0
    while i < len(out):
        child = out[i]
        for pid in known:
            if pid != child and child in _raw_extra_refs(pid, cfg):
                add(pid)
        i += 1

    return out
