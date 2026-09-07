"""可安装 memory maintenance 模块入口的兼容行为测试。"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from codev_platform.agent import deps
from codev_platform.agent import memory_maintenance as maintenance_impl
from codev_platform.ops import memory_maintenance


class _Store:
    def __init__(self, accepted: bool) -> None:
        self.accepted = accepted
        self.keys: list[int] = []

    @contextmanager
    def advisory_lock(self, key: int):
        self.keys.append(key)
        yield self.accepted


class _Maintenance:
    def __init__(self) -> None:
        self.archived = 0
        self.compressed: list[tuple[str, str, object, str]] = []

    def archive_expired(self) -> int:
        self.archived += 1
        return 3

    def compress_scope(self, scope: str, scope_ref: str, fuse, *, org_id: str):
        self.compressed.append((scope, scope_ref, fuse, org_id))
        return {"topic": "summary-id"}


def _wire(monkeypatch, maint: _Maintenance | None, store: _Store | None) -> None:
    monkeypatch.setattr(deps, "get_memory_maintenance", lambda: maint)
    monkeypatch.setattr(deps, "get_memory_store", lambda: store)


def test_import_does_not_replace_stdout() -> None:
    original = sys.stdout
    importlib.reload(memory_maintenance)
    assert sys.stdout is original


def test_disabled_memory_returns_two(monkeypatch, capsys) -> None:
    _wire(monkeypatch, None, None)
    assert memory_maintenance.main([]) == 2
    assert "[ABORT] memory 未启用" in capsys.readouterr().err


def test_unavailable_lock_returns_zero_without_ttl(monkeypatch, capsys) -> None:
    maint = _Maintenance()
    store = _Store(False)
    _wire(monkeypatch, maint, store)
    assert memory_maintenance.main([]) == 0
    assert maint.archived == 0
    assert store.keys == [0x6D656D31]
    assert "[SKIP]" in capsys.readouterr().out


def test_ttl_always_runs_when_lock_is_owned(monkeypatch, capsys) -> None:
    maint = _Maintenance()
    store = _Store(True)
    _wire(monkeypatch, maint, store)
    assert memory_maintenance.main([]) == 0
    assert maint.archived == 1
    assert maint.compressed == []
    output = capsys.readouterr().out
    assert "[TTL] 归档过期记忆 3 条" in output
    assert "未给 <scope> <scope_ref>" in output


def test_single_scope_argument_does_not_compress(monkeypatch) -> None:
    maint = _Maintenance()
    _wire(monkeypatch, maint, _Store(True))
    assert memory_maintenance.main(["personal"]) == 0
    assert maint.archived == 1
    assert maint.compressed == []


def test_provider_unavailable_skips_only_compression(monkeypatch, capsys) -> None:
    maint = _Maintenance()
    _wire(monkeypatch, maint, _Store(True))

    def unavailable():
        raise RuntimeError("provider missing")

    monkeypatch.setattr(deps, "get_provider", unavailable)
    assert memory_maintenance.main(["personal", "alice"]) == 0
    assert maint.archived == 1
    assert maint.compressed == []
    assert "[COMPRESS] 跳过:provider 不可用(provider missing)" in capsys.readouterr().err


def test_scope_arguments_and_org_are_forwarded(monkeypatch, capsys) -> None:
    maint = _Maintenance()
    _wire(monkeypatch, maint, _Store(True))
    provider = object()
    fuse = object()
    monkeypatch.setattr(deps, "get_provider", lambda: provider)
    monkeypatch.setattr(maintenance_impl, "make_llm_fuse", lambda got: fuse if got is provider else None)
    assert memory_maintenance.main(["project", "demo", "org-a"]) == 0
    assert maint.compressed == [("project", "demo", fuse, "org-a")]
    assert "scope=project ref=demo org=org-a 融合 1 个 topic" in capsys.readouterr().out


def test_main_uses_process_arguments_when_argv_is_none(monkeypatch) -> None:
    maint = _Maintenance()
    _wire(monkeypatch, maint, _Store(True))
    fuse = object()
    monkeypatch.setattr(deps, "get_provider", lambda: object())
    monkeypatch.setattr(maintenance_impl, "make_llm_fuse", lambda provider: fuse)
    monkeypatch.setattr(sys, "argv", ["memory_maintenance", "personal", "alice"])
    assert memory_maintenance.main() == 0
    assert maint.compressed == [("personal", "alice", fuse, "default")]


def test_module_execution_uses_installed_entrypoint(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["CODEV_PLATFORM_CONFIG"] = str(tmp_path / "missing.json")
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, "-m", "codev_platform.ops.memory_maintenance"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )
    assert completed.returncode == 2
    assert "[ABORT] memory 未启用" in completed.stderr
