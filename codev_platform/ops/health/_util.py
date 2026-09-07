"""health 子包 —— 通用 helper + Report 缓冲 (从 ops/health.py 抽出, file-discipline §1)。

无业务判定, 纯工具: 文件计数 / mtime / 子进程探针脚本 / git / jsonl 迭代 / 进程列表。
Report 放这里(而非 __init__)使 _checks / _usage 可 import 它而不与 __init__ 形成循环。
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from codev_platform.ops._common import out, run


# ----------------------------------------------------------------------
# report buffer (parity with .ps1 Line/Section + top banner)
# ----------------------------------------------------------------------
class Report:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.red = 0
        self.amber = 0

    def line(self, tag: str, status: str, msg: str) -> None:
        if status == "WARN":
            self.amber += 1
        elif status == "FAIL":
            self.red += 1
        self.rows.append({"tag": tag, "status": status, "msg": msg})

    def section(self, text: str) -> None:
        self.rows.append({"section": text})

    def flush(self) -> None:
        for row in self.rows:
            if "section" in row:
                out(row["section"])
            else:
                out("[" + row["status"].ljust(4) + "] " + row["tag"].ljust(20) + " " + row["msg"])


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _expand(p: str | None) -> Path | None:
    if not p:
        return None
    return Path(str(p)).expanduser()


def _count_files(d: Path, recurse: bool = False) -> int:
    if not d.is_dir():
        return 0
    it = d.rglob("*") if recurse else d.iterdir()
    return sum(1 for f in it if f.is_file())


def _latest_mtime(d: Path, suffixes: tuple[str, ...] | None = None) -> tuple[Path, float] | None:
    """(path, mtime) of newest file under d (optionally filtered by suffix)."""
    if not d.is_dir():
        return None
    best: tuple[Path, float] | None = None
    for f in d.rglob("*"):
        try:
            if not f.is_file():
                continue
            if suffixes and f.suffix.lower() not in suffixes:
                continue
            m = f.stat().st_mtime
        except OSError:
            continue
        if best is None or m > best[1]:
            best = (f, m)
    return best


def _run_py(py: Path, code: str, timeout: int = 60) -> tuple[int, str]:
    """Run a probe script with the given interpreter; return (rc, combined output).

    Uses _common.run for cross-platform launching; merges stderr into stdout so a
    crash message surfaces (mirrors the .ps1 `cmd /c "... 2>&1"`).
    """
    tmp = Path(tempfile.gettempdir()) / f"cdv_health_{os.getpid()}_{abs(hash(code)) & 0xffffff:x}.py"
    tmp.write_text(code, encoding="utf-8")
    try:
        cp = run(
            [str(py), str(tmp)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
        return cp.returncode, ((cp.stdout or "") + (cp.stderr or "")).strip()
    except Exception as exc:  # noqa: BLE001 - probe failures are non-fatal
        return 2, f"ERR {exc!r}"
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _git(repo: Path, *args: str) -> tuple[int, str]:
    try:
        cp = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        return cp.returncode, (cp.stdout or "").strip()
    except FileNotFoundError:
        return 127, ""


def _parse_dt(text: str) -> datetime | None:
    """Tolerant ISO-ish parser for jsonl ts / build_meta last_build_at."""
    text = text.strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            if fmt is None:
                dt = datetime.fromisoformat(text)
            else:
                dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=None)
        except ValueError:
            continue
    return None


def _iter_jsonl(path: Path):
    try:
        with path.open(encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    yield json.loads(ln)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def _list_processes() -> list[dict[str, str]]:
    """Best-effort cross-platform process list: [{pid, ppid, name, cmdline}].

    Prefers psutil; falls back to `ps` on POSIX / `wmic` on Windows. Returns [] if
    nothing works (callers then emit INFO 'process probe skipped').
    """
    try:
        import psutil  # type: ignore

        rows = []
        for p in psutil.process_iter(["pid", "ppid", "name", "cmdline"]):
            info = p.info
            rows.append({
                "pid": str(info.get("pid") or ""),
                "ppid": str(info.get("ppid") or ""),
                "name": (info.get("name") or ""),
                "cmdline": " ".join(info.get("cmdline") or []),
            })
        return rows
    except Exception:
        pass
    if os.name != "nt":
        try:
            cp = subprocess.run(
                ["ps", "-eo", "pid=,ppid=,comm=,args="],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            )
            rows = []
            for ln in cp.stdout.splitlines():
                parts = ln.split(None, 3)
                if len(parts) >= 3:
                    rows.append({
                        "pid": parts[0], "ppid": parts[1],
                        "name": parts[2], "cmdline": parts[3] if len(parts) > 3 else "",
                    })
            return rows
        except Exception:
            return []
    # Windows without psutil: PowerShell CIM -> JSON (wmic is removed on recent
    # Win11; CIM is the reliable path). Emit one object per process.
    ps_script = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,Name,CommandLine | "
        "ConvertTo-Json -Compress -Depth 2"
    )
    try:
        cp = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        )
        data = json.loads(cp.stdout) if cp.stdout.strip() else []
        if isinstance(data, dict):
            data = [data]
        return [{
            "pid": str(d.get("ProcessId") or ""),
            "ppid": str(d.get("ParentProcessId") or ""),
            "name": d.get("Name") or "",
            "cmdline": d.get("CommandLine") or "",
        } for d in data]
    except Exception:
        return []
