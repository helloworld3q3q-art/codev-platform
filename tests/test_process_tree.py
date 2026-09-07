from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from codev_platform.core import process_tree
from codev_platform.core.process_tree import kill_process_tree, popen_tree, run_tree


class _ProcessStub:
    pid = 4242

    def __init__(self, returncode: int | None) -> None:
        self._returncode = returncode

    def poll(self) -> int | None:
        return self._returncode


def _capture_posix_group_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[int, int]]:
    signals: list[tuple[int, int]] = []

    class _PosixOS:
        name = "posix"

        @staticmethod
        def killpg(pid: int, sig: int) -> None:
            signals.append((pid, sig))

    class _Signal:
        SIGKILL = 9

    monkeypatch.setattr(process_tree, "os", _PosixOS)
    monkeypatch.setattr(process_tree, "signal", _Signal)
    return signals


def test_run_tree_timeout_kills_descendant_holding_stdout(tmp_path: Path) -> None:
    marker_path = tmp_path / "descendant-survived.txt"
    child_script = (
        "import pathlib, time;"
        "print('child holding stdout', flush=True);"
        "time.sleep(1.2);"
        f"pathlib.Path({str(marker_path)!r}).write_text('alive', encoding='utf-8')"
    )
    script = (
        "import subprocess, sys, time;"
        f"subprocess.Popen([sys.executable, '-c', {child_script!r}]);"
        "print('parent waiting', flush=True);"
        "time.sleep(6)"
    )

    start = time.perf_counter()
    with pytest.raises(subprocess.TimeoutExpired):
        run_tree([sys.executable, "-c", script], timeout=0.2, capture_output=True, text=True)
    elapsed = time.perf_counter() - start

    assert elapsed < 3
    time.sleep(1.5)
    assert not marker_path.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="需要 Linux /proc 进程组身份")
def test_run_tree父进程先退出时仍按原进程组身份清算后代(tmp_path: Path) -> None:
    marker_path = tmp_path / "orphan-descendant-survived.txt"
    child_script = (
        "import pathlib, time;"
        "print('orphan holding stdout', flush=True);"
        "time.sleep(1.2);"
        f"pathlib.Path({str(marker_path)!r}).write_text('alive', encoding='utf-8')"
    )
    script = (
        "import subprocess, sys;"
        f"subprocess.Popen([sys.executable, '-c', {child_script!r}]);"
        "print('parent exited', flush=True)"
    )

    with pytest.raises(subprocess.TimeoutExpired):
        run_tree(
            [sys.executable, "-c", script],
            timeout=0.2,
            cleanup_timeout_sec=1.0,
            capture_output=True,
            text=True,
        )

    time.sleep(1.5)
    assert not marker_path.exists()


def test_run_tree_returns_completed_process_for_captured_output() -> None:
    completed = run_tree(
        [sys.executable, "-c", "print('ok')"],
        timeout=5,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout == "ok\n"
    assert completed.stderr == ""


def test_kill_process_tree_terminates_live_process() -> None:
    proc = popen_tree(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    kill_process_tree(proc)
    proc.wait(timeout=5)

    assert proc.poll() is not None


def test_kill_process_tree_posix_does_not_signal_reaped_process(monkeypatch) -> None:
    signals = _capture_posix_group_signals(monkeypatch)

    kill_process_tree(_ProcessStub(returncode=0))

    assert signals == []


def test_kill_process_tree_posix_signals_group_for_live_process(monkeypatch) -> None:
    signals = _capture_posix_group_signals(monkeypatch)

    kill_process_tree(_ProcessStub(returncode=None))

    assert signals == [(4242, 9)]


def test_run_tree_timeout_cleanup_has_bound_and_never_uses_popen_context(
    monkeypatch,
) -> None:
    class _Process:
        args = ("fake",)
        returncode = None

        def __init__(self) -> None:
            self.communicate_timeouts: list[float | None] = []
            self.exit_calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            self.exit_calls += 1

        def communicate(self, *, input=None, timeout=None):
            self.communicate_timeouts.append(timeout)
            raise subprocess.TimeoutExpired(self.args, timeout, output=b"partial")

    proc = _Process()
    kill_timeouts: list[float] = []
    monkeypatch.setattr(process_tree, "popen_tree", lambda *_args, **_kwargs: proc)
    monkeypatch.setattr(
        process_tree,
        "kill_process_tree",
        lambda _proc, *, timeout=5.0: kill_timeouts.append(timeout),
    )

    with pytest.raises(subprocess.TimeoutExpired):
        process_tree.run_tree(
            ["fake"],
            timeout=0.01,
            cleanup_timeout_sec=0.02,
            capture_output=True,
        )

    assert proc.exit_calls == 0
    assert proc.communicate_timeouts[0] == 0.01
    assert proc.communicate_timeouts[1] is not None
    assert 0 <= proc.communicate_timeouts[1] <= 0.02
    assert len(kill_timeouts) == 1
    assert 0 <= kill_timeouts[0] <= 0.02


def test_run_tree_cleanup_errors_do_not_replace_original_timeout(monkeypatch) -> None:
    class _Process:
        args = ("fake",)
        returncode = None

        def __init__(self) -> None:
            self.calls = 0

        def communicate(self, *, input=None, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(self.args, timeout, output=b"partial")
            raise RuntimeError("cleanup pipe failed")

    proc = _Process()
    monkeypatch.setattr(process_tree, "popen_tree", lambda *_args, **_kwargs: proc)
    monkeypatch.setattr(
        process_tree,
        "kill_process_tree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("kill failed")),
    )

    with pytest.raises(subprocess.TimeoutExpired) as captured:
        process_tree.run_tree(
            ["fake"],
            timeout=0.01,
            cleanup_timeout_sec=0.02,
            capture_output=True,
        )

    assert captured.value.output == b"partial"
