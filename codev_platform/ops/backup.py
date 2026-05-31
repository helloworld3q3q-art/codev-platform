"""codev_platform.ops.backup -- snapshot platform state to timestamped dirs.

  codev-platform backup [--out DIR] [--keep N] [--dry-run] [--gitea DIR]

Pure logic (plan_backup / prune_old) is split from side effects (run_backup)
so the planning + retention math is unit-testable without subprocess/IO.

Backup items (BackupItem):
  - pg   : pg_dump of memory.pg_dsn (skipped when dsn empty -- not in plan)
  - tree : a directory / file under data.platform_data_dir, or the gitea dir
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tarfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


BACKUP_DIR_RE = re.compile(r"^codev-backup-\d{8}-\d{6}$")
_DATA_SUBITEMS = ("chroma", "cross_layer.sqlite", "codegraph_ext", "audit")


def _out(msg: str = "") -> None:
    print(msg, flush=True)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _redact_dsn(dsn: str | None) -> str | None:
    """掩码 dsn 的密码段 —— manifest / dry-run / 日志一律不落明文密码(安全红线)。

    `postgresql://user:secret@host:5432/db` → `postgresql://user:***@host:5432/db`。
    无密码段原样返回;解析失败整体掩码兜底。单一真值源, 凡要展示/落盘 dsn 都过它。
    """
    if not dsn:
        return dsn
    try:
        from urllib.parse import urlsplit, urlunsplit
        p = urlsplit(dsn)
        if not p.password:
            return dsn
        host = p.hostname or ""
        netloc = f"{p.username}:***@{host}" if p.username else f"***@{host}"
        if p.port:
            netloc = f"{netloc}:{p.port}"
        return urlunsplit((p.scheme, netloc, p.path, p.query, p.fragment))
    except Exception:
        return "<redacted-dsn>"


@dataclass
class BackupItem:
    kind: str          # "pg" | "tree"
    label: str         # archive base name (no extension)
    source: str | None = None   # tree: source path; pg: None
    dsn: str | None = None       # pg: connection string; tree: None


def plan_backup(cfg, ts: str, gitea_dir: str | None = None) -> list[BackupItem]:
    """Pure: derive the backup item list from config (no IO side effects).

    - memory.pg_dsn non-empty -> one pg item; empty -> skipped (not in plan).
    - data.platform_data_dir -> a tree item per existing _DATA_SUBITEMS child.
    - gitea_dir (arg or config backup.gitea_dir) non-empty & existing -> tree item.
    """
    from codev_platform.core.config import get

    items: list[BackupItem] = []

    dsn = get(cfg, "memory.pg_dsn")
    if dsn:
        items.append(BackupItem(kind="pg", label="memory-pg", dsn=dsn))

    data_dir = get(cfg, "data.platform_data_dir")
    if data_dir:
        base = Path(data_dir).expanduser()
        for child in _DATA_SUBITEMS:
            p = base / child
            if p.exists():
                items.append(BackupItem(kind="tree", label=f"data-{child}", source=str(p)))

    gd = gitea_dir if gitea_dir else get(cfg, "backup.gitea_dir")
    if gd and Path(gd).expanduser().exists():
        items.append(BackupItem(kind="tree", label="gitea", source=str(Path(gd).expanduser())))

    return items


def prune_old(existing_dirs: list[str], keep: int) -> list[str]:
    """Pure: return backup dir names that should be DELETED.

    Keeps the newest ``keep`` (by name desc, names embed sortable ts). keep<=0
    means never prune (returns []). Non-backup names are ignored (never deleted).
    """
    if keep <= 0:
        return []
    backups = sorted((d for d in existing_dirs if BACKUP_DIR_RE.match(d)), reverse=True)
    return backups[keep:]


def _archive_tree(source: Path, dest_base: Path) -> str:
    """Tar.gz a dir, or copy a single file. Returns the written path."""
    if source.is_dir():
        out = dest_base.with_suffix(".tar.gz")
        with tarfile.open(out, "w:gz") as tar:
            tar.add(source, arcname=source.name)
        return str(out)
    out = dest_base.with_suffix(source.suffix)
    shutil.copy2(source, out)
    return str(out)


def run_backup(cfg, out_dir: Path, keep: int, dry_run: bool, gitea_dir: str | None) -> int:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    plan = plan_backup(cfg, ts, gitea_dir=gitea_dir)

    if not plan:
        _out("(无可备份项: memory.pg_dsn 为空且 data.platform_data_dir 无子项 / 未配)")

    if dry_run:
        _out(f"[dry-run] 备份目录: {out_dir / ('codev-backup-' + ts)}")
        for it in plan:
            tgt = _redact_dsn(it.dsn) if it.kind == "pg" else it.source
            _out(f"  [{it.kind}] {it.label}  <- {tgt}")
        return 0

    backup_dir = out_dir / f"codev-backup-{ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    for it in plan:
        try:
            if it.kind == "pg":
                out_path = backup_dir / f"{it.label}.sql"
                # list args, no shell -- defends against DSN injection.
                with open(out_path, "wb") as fh:
                    proc = subprocess.run(
                        ["pg_dump", "--dbname", it.dsn or ""],
                        stdout=fh, stderr=subprocess.PIPE, check=False,
                    )
                if proc.returncode != 0:
                    results.append({"label": it.label, "kind": it.kind, "ok": False,
                                    "error": proc.stderr.decode("utf-8", "replace")[:500]})
                    _err(f"  FAIL [pg] {it.label}: pg_dump rc={proc.returncode}")
                    continue
                results.append({"label": it.label, "kind": it.kind, "ok": True, "path": str(out_path)})
                _out(f"  OK [pg] {it.label} -> {out_path.name}")
            else:
                written = _archive_tree(Path(it.source or ""), backup_dir / it.label)
                results.append({"label": it.label, "kind": it.kind, "ok": True, "path": written})
                _out(f"  OK [tree] {it.label} -> {Path(written).name}")
        except Exception as exc:  # one item failing must not abort the rest
            results.append({"label": it.label, "kind": it.kind, "ok": False, "error": str(exc)[:500]})
            _err(f"  FAIL [{it.kind}] {it.label}: {exc}")

    def _safe_item(i: BackupItem) -> dict:
        d = asdict(i)
        if d.get("dsn"):
            d["dsn"] = _redact_dsn(d["dsn"])  # 不把明文密码写进 manifest.json
        return d

    manifest = {"ts": ts, "items": [_safe_item(i) for i in plan], "results": results}
    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _out(f"OK: 备份 {len(plan)} 项 -> {backup_dir}  (manifest.json 已写)")

    # retention
    existing = [d.name for d in out_dir.iterdir() if d.is_dir()] if out_dir.exists() else []
    for victim in prune_old(existing, keep):
        shutil.rmtree(out_dir / victim, ignore_errors=True)
        _out(f"  prune: 删旧备份 {victim}")

    return 0 if all(r["ok"] for r in results) else 1


def _default_out_dir(cfg) -> Path:
    from codev_platform.core.config import get
    cfg_out = get(cfg, "backup.out_dir")
    if cfg_out:
        return Path(cfg_out).expanduser()
    from codev_platform.core.paths import data_root
    return data_root().parent / "backups"


def cmd_backup(args: argparse.Namespace) -> int:
    from codev_platform.core.config import load_config
    cfg = load_config()
    out_dir = Path(args.out).expanduser() if args.out else _default_out_dir(cfg)
    return run_backup(cfg, out_dir, args.keep, args.dry_run, args.gitea)


def register(subparsers) -> None:
    bp = subparsers.add_parser("backup", help="备份平台状态 (memory PG + data 子目录 + gitea) 到带时间戳目录")
    bp.add_argument("--out", default=None, help="备份输出目录 (默认 config backup.out_dir 或 data_root/../backups)")
    bp.add_argument("--keep", type=int, default=7, help="保留最新 N 份, 其余清理 (默认 7; <=0 不清理)")
    bp.add_argument("--dry-run", action="store_true", help="只打印 plan 不执行")
    bp.add_argument("--gitea", default=None, help="额外备份的 gitea 数据目录 (覆盖 config backup.gitea_dir)")
    bp.set_defaults(func=cmd_backup)
