"""CLI `index status` —— 统一索引构建 manifest 的读侧 (roadmap-2026-06-07 Phase 1)。

回答 plan Phase 1 Gate: "validate 或新增 index status 能看到每类索引 freshness"。
读 data_root/index_manifest.sqlite, 每个 project 列各类索引最近构建状态 + 是否对齐 HEAD。
纯读, 不触发构建。
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def _repo_path(projects: dict, pid: str, cfg: dict) -> Path | None:
    pc = projects.get(pid)
    if isinstance(pc, dict) and pc.get("repo_path"):
        p = Path(pc["repo_path"]).expanduser()
        return p if p.exists() else None
    try:
        from codev_platform.core.repos import project_repo_specs
        for spec in project_repo_specs(pid, cfg=cfg):
            if spec.is_main and spec.root.exists():
                return spec.root
    except Exception:  # noqa: BLE001 - status should degrade to HEAD unknown
        return None
    return None


def _age(finished_at: float | None, now: float) -> str:
    if not finished_at:
        return "?"
    sec = max(0, int(now - finished_at))
    if sec < 90:
        return f"{sec}s 前"
    if sec < 5400:
        return f"{sec // 60}m 前"
    if sec < 172800:
        return f"{sec // 3600}h 前"
    return f"{sec // 86400}d 前"


def cmd_index_status(args: argparse.Namespace) -> int:
    from codev_platform.core.config import get, load_config
    from codev_platform.index_manifest import freshness, read_manifest

    cfg = load_config()
    projects = get(cfg, "projects") or {}
    recs = read_manifest(args.project)
    pids = sorted({r.project_id for r in recs})
    if not pids:
        print("(index manifest 为空 —— 还没有索引构建被记录; reindex 跑过后即有记录)")
        return 0

    report = {pid: freshness(pid, _repo_path(projects, pid, cfg)) for pid in pids}

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    now = time.time()
    stale = 0
    for pid in pids:
        head = report[pid][0]["head"] if report[pid] else None
        head_s = f"HEAD@{head[:8]}" if head else "HEAD 未知(无 repo_path 或非 git)"
        print(f"\n=== {pid} === ({head_s})")
        for row in report[pid]:
            fresh = row["fresh"]
            mark = "对齐 ✓" if fresh is True else ("落后 ✗" if fresh is False else "未知 ?")
            if fresh is False:
                stale += 1
            commit = (row["git_commit"] or "")[:8] or "—"
            print(f"  {row['kind']:10} {row['status']:6} {mark:7} "
                  f"commit={commit} {_age(row['finished_at'], now)}  {row['reason']}")
    if stale:
        print(f"\n⚠️ {stale} 个索引落后于 HEAD — 跑 `codev-platform reindex` 重建。")
    return 0


def register(subparsers) -> None:
    p = subparsers.add_parser("index", help="索引构建 manifest (新鲜度 / 状态)")
    isub = p.add_subparsers(dest="index_cmd", required=True)
    sp = isub.add_parser("status", help="每类索引最近构建状态 + 是否对齐 HEAD")
    sp.add_argument("--project", default=None, help="只看某 project_id (默认全部已记录的)")
    sp.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    sp.set_defaults(func=cmd_index_status)
