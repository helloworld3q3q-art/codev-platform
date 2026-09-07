"""Regression tests for codev_platform.ops._common.resolve_argv.

Bug context: Python 3.12+ refuses to exec Windows script launchers (.cmd/.bat/.ps1)
from a list argv, so mvn/pnpm/scripts must be routed through cmd /c or powershell
-File. POSIX / real executables must run directly.
"""
from __future__ import annotations

import codev_platform.ops._common as common


def _which_factory(mapping):
    """shutil.which stub: returns mapped absolute path, else None."""
    def _which(name):
        return mapping.get(name)
    return _which


def test_cmd_routed_through_comspec(monkeypatch):
    monkeypatch.setattr(common.os, "name", "nt")
    monkeypatch.setattr(common.shutil, "which", _which_factory({"mvn": r"C:\bin\mvn.cmd"}))
    monkeypatch.setenv("COMSPEC", "cmd.exe")
    argv = common.resolve_argv(["mvn", "test"])
    assert argv == ["cmd.exe", "/c", r"C:\bin\mvn.cmd", "test"]


def test_bat_routed_through_comspec(monkeypatch):
    monkeypatch.setattr(common.os, "name", "nt")
    monkeypatch.setattr(common.shutil, "which", _which_factory({"foo": r"C:\bin\foo.bat"}))
    monkeypatch.setenv("COMSPEC", "cmd.exe")
    argv = common.resolve_argv(["foo", "-x"])
    assert argv == ["cmd.exe", "/c", r"C:\bin\foo.bat", "-x"]


def test_ps1_routed_through_powershell(monkeypatch):
    monkeypatch.setattr(common.os, "name", "nt")
    monkeypatch.setattr(common.shutil, "which", _which_factory({"hook": r"C:\hooks\hook.ps1"}))
    argv = common.resolve_argv(["hook", "--repo", "x"])
    assert argv == [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", r"C:\hooks\hook.ps1", "--repo", "x",
    ]


def test_exe_runs_directly_on_windows(monkeypatch):
    monkeypatch.setattr(common.os, "name", "nt")
    monkeypatch.setattr(common.shutil, "which", _which_factory({"python": r"C:\py\python.exe"}))
    argv = common.resolve_argv(["python", "-V"])
    assert argv == [r"C:\py\python.exe", "-V"]


def test_posix_runs_directly(monkeypatch):
    monkeypatch.setattr(common.os, "name", "posix")
    monkeypatch.setattr(common.shutil, "which", _which_factory({"mvn": "/usr/bin/mvn"}))
    # even a .cmd-looking name is not rerouted off Windows
    argv = common.resolve_argv(["mvn", "test"])
    assert argv == ["/usr/bin/mvn", "test"]


def test_posix_no_reroute_for_ps1(monkeypatch):
    monkeypatch.setattr(common.os, "name", "posix")
    monkeypatch.setattr(common.shutil, "which", _which_factory({}))
    argv = common.resolve_argv(["thing.ps1", "a"])
    # which returns None -> falls back to cmd[0]; posix never reroutes
    assert argv == ["thing.ps1", "a"]


def test_empty_argv_returned_as_is():
    assert common.resolve_argv([]) == []


def test_which_miss_falls_back_to_name(monkeypatch):
    monkeypatch.setattr(common.os, "name", "nt")
    monkeypatch.setattr(common.shutil, "which", _which_factory({}))
    # not found on PATH and no script suffix -> run the bare name directly
    argv = common.resolve_argv(["git", "status"])
    assert argv == ["git", "status"]
