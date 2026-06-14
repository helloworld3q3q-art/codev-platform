"""原子索引切换(atomic handoff)—— blue-green 双缓冲, 跨平台原子切换。

roadmap-2026-06-07 Phase 1 / 阶段一(纯核)。设计见
`docs/plans/roadmap-2026-06-07/phase1-atomic-handoff-plan-2026-06-14.md`。

**问题**: full rebuild 原地 rmtree chroma 库时, reader daemon 读到半成品/空库
(daily-summary-2026-06-12 §十.1 实证: 重建中途 SIGKILL 半写坏 → `database disk
image is malformed` → search_docs 全项目下线)。多 reader 并发(多机 arc)放大此窗口。

**做法**: writer 建到 side build 目录, 建成后原子切 current pointer; reader 读 pointer
指向的目录 → 全程读旧 build, 绝不撞半成品。失败的 side build 不影响 current。

**为什么 pointer 文件 + os.replace, 不用 symlink / 目录 rename**:
- symlink 在 Windows 需开发者模式/管理员 → 不可移植。
- rename 整个 build 目录覆盖非空 live → POSIX `ENOTEMPTY` / Windows 失败 → 不跨平台。
- pointer 文件 + `os.replace(tmp, current.json)`: POSIX `rename(2)` + Windows `ReplaceFile`
  都保证同卷原子替换单文件 → 跨平台稳, pointer 单一真值不半写。

**向后兼容(零迁移)**: `resolve_current` 无 `current.json` 时退回 base 本身 →
现有原地库继续工作, 直到第一次 full rebuild 才走新布局。

**串行假设**: 写侧(begin/commit/gc)串行(workflow §8 索引重建不并发), 故 commit 的
中转 tmp 用固定名不会撞。reader(resolve_current)只读, 任意并发安全。
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

_POINTER = "current.json"   # base 根下 pointer 文件(唯一真值)
_BUILDS = "builds"          # side build 子目录容器
_TMP = "current.json.tmp"   # 原子写中转(写侧串行, 固定名安全)


def _builds_root(base: Path) -> Path:
    return base / _BUILDS


def _pointer_path(base: Path) -> Path:
    return base / _POINTER


def _safe_id(build_id: str) -> str:
    """防路径穿越: build_id 不得含分隔符 / 父引用 / 空字节。"""
    bid = build_id.strip()
    if not bid or "/" in bid or "\\" in bid or bid in (".", "..") or "\x00" in bid:
        raise ValueError(f"非法 build_id: {build_id!r}")
    return bid


def new_build_id(commit: str | None, unique_suffix: str) -> str:
    """派生唯一 build_id = `<commit12>-<unique_suffix>`。

    纯函数(不内嵌 time/random 保可测): `unique_suffix` 由调用方传唯一值(时间戳/计数器)
    保证每次构建唯一 —— 故同 commit 多次 full rebuild **不会复用 id 撞掉正被 reader 读的
    current**。失败 side build 由 writer 异常路径显式 rmtree 清(不靠 gc)。"""
    head = ((commit or "nogit").strip()[:12]) or "nogit"
    return _safe_id(f"{head}-{unique_suffix}")


def read_pointer(base: Path) -> str | None:
    """当前 build_id(无 pointer / 损坏 json → None, fail-soft)。"""
    p = _pointer_path(base)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("build") or None
    except (OSError, ValueError):
        return None


def resolve_current(base: Path) -> Path:
    """reader 入口: 返回当前可用 build 目录。

    - 有 pointer 且目标存在 → `builds/<build_id>/`
    - 无 pointer(旧布局/首次)或目标缺失 → 退回 `base` 本身(100% 向后兼容)。

    只解析路径, 不创建目录。
    """
    build_id = read_pointer(base)
    if build_id:
        d = _builds_root(base) / build_id
        if d.is_dir():
            return d
    return base


def begin_build(base: Path, build_id: str) -> Path:
    """writer 入口(full rebuild): 返回干净的 side build 目录 `builds/<build_id>/`。

    同 build_id 已存在(同次重试)→ 先清空。**不动 current pointer** → reader 仍读旧 build。
    """
    d = _builds_root(base) / _safe_id(build_id)
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True, exist_ok=True)
    return d


def commit_build(base: Path, build_id: str) -> None:
    """原子切 current → build_id(写 tmp pointer + `os.replace`)。

    要求 `builds/<build_id>/` 已存在(writer 建好)。commit 后 reader 新连接即读新 build,
    持旧连接的 reader 继续读旧 build(由 gc keep≥2 保命)。
    """
    bid = _safe_id(build_id)
    if not (_builds_root(base) / bid).is_dir():
        raise FileNotFoundError(f"build 目录不存在, 不能 commit: {_builds_root(base) / bid}")
    base.mkdir(parents=True, exist_ok=True)
    tmp = base / _TMP
    tmp.write_text(json.dumps({"build": bid}), encoding="utf-8")
    os.replace(tmp, _pointer_path(base))   # 跨平台原子替换单文件


def gc_builds(base: Path, *, keep: int = 2) -> list[str]:
    """删旧 build, 按 mtime 新→旧保最近 keep 个; **current 永不删**(防删正读的库)。

    返回删掉的 build_id 列表。`builds/` 不存在 → 空列表。
    """
    root = _builds_root(base)
    if not root.is_dir():
        return []
    current = read_pointer(base)
    dirs = [d for d in root.iterdir() if d.is_dir()]
    dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)   # 新→旧
    survivors = {d.name for d in dirs[:keep]} if keep > 0 else set()
    if current:
        survivors.add(current)   # current 即便落在 keep 窗口外也保
    removed: list[str] = []
    for d in dirs:
        if d.name in survivors:
            continue
        try:
            shutil.rmtree(d)
            removed.append(d.name)
        except OSError:
            # Windows: reader(daemon)仍持旧 build 的 sqlite 句柄 → rmtree PermissionError(WinError 32)。
            # **fail-soft 跳过**(下次 gc 再回收, 或 daemon 重启释放句柄后): gc 是清理非关键路径,
            # 绝不能因删不掉旧 build 而抛出阻断本次重建(commit_build 已成功, current 已切)。
            pass
    return removed
