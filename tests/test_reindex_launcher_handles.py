"""后台启动器不得继承调用方管道句柄的回归测试。"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform import mcp_runtime


_FIXTURE = Path(__file__).parent / "fixtures" / "reindex_process_fixture.py"


@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 具有目标句柄继承语义")
def test_spawn_detached_does_not_hold_outer_capture_pipe(tmp_path: Path) -> None:
    code = "\n".join(
        [
            "import os, sys",
            "from pathlib import Path",
            "from codev_platform import mcp_runtime",
            "mcp_runtime.spawn_detached(",
            "    [sys.executable, '-c', 'import time; time.sleep(2)'],",
            "    os.getcwd(), Path(sys.argv[1]), os.environ.copy(),",
            ")",
            "print('outer-done', flush=True)",
        ]
    )
    completed = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "daemon.log")],
        capture_output=True,
        text=True,
        timeout=0.8,
        check=True,
    )

    assert completed.stdout.strip() == "outer-done"


def test_mcp_runtime_spawn_detached_preserves_process_arguments(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return SimpleNamespace(pid=4321)

    monkeypatch.setattr(mcp_runtime.subprocess, "Popen", fake_popen)
    env = {"RUNTIME_TEST": "1"}
    command = [sys.executable, "-c", "pass"]
    log_path = tmp_path / "daemon.log"

    pid = mcp_runtime.spawn_detached(command, str(tmp_path), log_path, env)

    assert pid == 4321
    assert captured["argv"] == command
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["stdout"] is captured["stderr"]
    assert Path(captured["stdout"].name) == log_path
    assert captured["cwd"] == str(tmp_path)
    assert captured["env"] is env
    expected_flags = (0x08000000 | 0x00000200) if sys.platform == "win32" else 0
    assert captured["creationflags"] == expected_flags
    assert captured["start_new_session"] is (sys.platform != "win32")
    assert captured["close_fds"] is True


def test_worker_launcher_spawn_preserves_environment_and_detached_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from codev_platform.reindex import worker_launcher

    captured: dict[str, object] = {}

    def fake_spawn(cmd, cwd, log_path, env=None):
        captured.update({"cmd": cmd, "cwd": cwd, "log_path": log_path, "env": env})
        return 2468

    monkeypatch.setattr(mcp_runtime, "spawn_detached", fake_spawn)
    command = [sys.executable, "-m", "codev_platform.cli"]
    environment = {"WORKER_TEST": "1"}
    log_path = tmp_path / "worker.log"

    pid = worker_launcher.spawn_worker_process(command, str(tmp_path), log_path, env=environment)

    assert pid == 2468
    assert captured == {
        "cmd": command,
        "cwd": str(tmp_path),
        "log_path": log_path,
        "env": environment,
    }


def test_chroma_launcher_preserves_daemon_arguments(tmp_path: Path, monkeypatch) -> None:
    venv = tmp_path / "platform-docs-venv"
    python = venv / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setenv("PLATFORM_DOCS_VENV", str(venv))
    sys.modules.pop("codev_platform.chroma.launcher", None)
    launcher = importlib.import_module("codev_platform.chroma.launcher")
    captured: dict[str, object] = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return SimpleNamespace(pid=9876)

    log_path = tmp_path / "chroma.log"
    monkeypatch.setattr(launcher, "DAEMON_LOG", log_path)
    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)

    launcher._spawn_daemon()

    assert captured["argv"] == [
        str(python),
        "-m",
        "codev_platform.chroma.daemon_entry",
        "--http",
    ]
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["stdout"] is captured["stderr"]
    assert Path(captured["stdout"].name) == log_path
    assert captured["cwd"] == str(launcher.SCRIPT_DIR)
    expected_flags = (0x08000000 | 0x00000200) if sys.platform == "win32" else 0
    assert captured["creationflags"] == expected_flags
    assert captured["env"]["PLATFORM_DOCS_VENV"] == str(venv)
    assert captured["close_fds"] is True


def test_process_fixture_success_mode() -> None:
    completed = subprocess.run(
        [sys.executable, str(_FIXTURE), "success", "--seconds", "0"],
        capture_output=True,
        text=True,
        timeout=3,
        check=True,
    )

    assert completed.stdout == "proof: success\n"
