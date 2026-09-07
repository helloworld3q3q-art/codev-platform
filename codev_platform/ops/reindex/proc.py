"""reindex.proc -- subprocess spawn / foreground-run / health-refresh helpers.

Pure-move split out of the original ops/reindex.py (no logic change). These
back the legacy detached-spawn path; the queue-based dispatch no longer calls
them directly but they remain part of the module surface.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from codev_platform.ops import _common as C

from .logs import _append_log


def _status_label(rc: int) -> str:
    if rc == 0:
        return "ok"
    if rc == 2:
        return "warn exit=2"
    return f"failed exit={rc}"


def _finish_log(log_file: Path, rc: int) -> None:
    from .logs import _now
    _append_log(log_file, f"===== reindex finished at {_now()} [{_status_label(rc)}] =====\n")


def _run_logged_foreground(cmd: list[str], log_file: Path) -> int:
    """Run reindex inline, appending stdout+stderr to the log (UTF-8)."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a", encoding="utf-8") as fh:
        cp = subprocess.run(C.resolve_argv(cmd), stdout=fh, stderr=subprocess.STDOUT)
    return cp.returncode


def _run_health_refresh(cmd: list[str], log_file: Path) -> None:
    """Refresh the widget health snapshot, best-effort (never raises)."""
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as fh:
            subprocess.run(C.resolve_argv(cmd), stdout=fh, stderr=subprocess.STDOUT)
    except Exception as exc:  # noqa: BLE001 - snapshot refresh must never fail the hook
        _append_log(log_file, f"health snapshot refresh exception: {exc}\n")


def _spawn_background(cmd: list[str], log_file: Path, post_cmd: list[str] | None = None) -> None:
    """Spawn reindex detached, redirecting output to the log + writing the
    finish marker afterwards. Uses a tiny Python wrapper so the finish status
    is recorded cross-platform (replaces the .ps1 temp-wrapper trick)."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    py = sys.executable or "python"
    inner = repr(C.resolve_argv(cmd))
    post = repr(C.resolve_argv(post_cmd) if post_cmd else None)
    logf = repr(str(log_file))
    wrapper = (
        "import subprocess,sys\n"
        f"_cmd={inner}\n"
        f"_post={post}\n"
        f"_log={logf}\n"
        "import datetime\n"
        "def _stamp(): return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')\n"
        "rc=-1\n"
        "try:\n"
        "    with open(_log,'a',encoding='utf-8') as fh:\n"
        "        rc=subprocess.run(_cmd,stdout=fh,stderr=subprocess.STDOUT).returncode\n"
        "except Exception as e:\n"
        "    with open(_log,'a',encoding='utf-8') as fh:\n"
        "        fh.write('reindex wrapper exception: '+str(e)+chr(10))\n"
        "lbl='ok' if rc==0 else ('warn exit=2' if rc==2 else 'failed exit='+str(rc))\n"
        "with open(_log,'a',encoding='utf-8') as fh:\n"
        "    fh.write('===== reindex finished at '+_stamp()+' ['+lbl+'] ====='+chr(10))\n"
        "if _post:\n"
        "    try:\n"
        "        with open(_log,'a',encoding='utf-8') as fh:\n"
        "            subprocess.run(_post,stdout=fh,stderr=subprocess.STDOUT)\n"
        "    except Exception as e:\n"
        "        with open(_log,'a',encoding='utf-8') as fh:\n"
        "            fh.write('health snapshot refresh exception: '+str(e)+chr(10))\n"
    )
    kwargs: dict = {}
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NO_WINDOW
        kwargs["creationflags"] = 0x00000008 | 0x08000000
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(
        [py, "-c", wrapper],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )
