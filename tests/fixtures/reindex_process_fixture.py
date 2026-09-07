"""重建索引进程控制测试使用的独立夹具。"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path


def _hold_pipe(seconds: float) -> int:
    subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({seconds!r})"],
        close_fds=False,
    )
    return 0


def _ignore_term(seconds: float) -> int:
    if os.name != "nt":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    print("fixture stdout", flush=True)
    print("fixture stderr", file=sys.stderr, flush=True)
    time.sleep(seconds)
    return 0


def _term_exit(seconds: float) -> int:
    if os.name != "nt":
        signal.signal(signal.SIGTERM, lambda *_args: raise_exit())
    time.sleep(seconds)
    return 0


def raise_exit() -> None:
    raise SystemExit(0)


def _grandchild(seconds: float, state_path: Path, *, escape_session: bool) -> int:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child_script = (
        f"import signal,time;signal.signal(signal.SIGTERM, signal.SIG_IGN);time.sleep({seconds!r})"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", child_script],
        start_new_session=escape_session,
    )
    state_path.write_text(str(child.pid), encoding="ascii")
    time.sleep(seconds)
    return 0


def _write_cgroup_probe(state_path: Path, seconds: float) -> int:
    membership = Path("/proc/self/cgroup").read_text(encoding="ascii")
    state_path.write_text(membership, encoding="ascii")
    time.sleep(seconds)
    return 0


def _fast_orphan(seconds: float, state_path: Path) -> int:
    intermediate = os.fork()
    if intermediate == 0:
        grandchild = os.fork()
        if grandchild > 0:
            os._exit(0)
        os.setsid()
        state_path.write_text(str(os.getpid()), encoding="ascii")
        time.sleep(seconds)
        os._exit(0)
    os.waitpid(intermediate, 0)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=(
            "cgroup-probe",
            "fast-setsid-orphan",
            "group-grandchild",
            "hold-pipe",
            "ignore-term",
            "setsid-grandchild",
            "success",
            "term-exit",
        ),
    )
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--state-path", type=Path)
    args = parser.parse_args(argv)
    if args.mode == "hold-pipe":
        return _hold_pipe(args.seconds)
    if args.mode == "ignore-term":
        return _ignore_term(args.seconds)
    if args.mode == "term-exit":
        return _term_exit(args.seconds)
    if args.mode in {"group-grandchild", "setsid-grandchild"}:
        if args.state_path is None:
            return 2
        return _grandchild(
            args.seconds,
            args.state_path,
            escape_session=args.mode == "setsid-grandchild",
        )
    if args.mode == "cgroup-probe":
        if args.state_path is None:
            return 2
        return _write_cgroup_probe(args.state_path, args.seconds)
    if args.mode == "fast-setsid-orphan":
        if args.state_path is None:
            return 2
        return _fast_orphan(args.seconds, args.state_path)
    print("proof: success", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
