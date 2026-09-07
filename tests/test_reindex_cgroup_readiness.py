from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest

from codev_platform.reindex.attempt_process import (
    Deadline,
    ProcessBackendReadinessError,
)
from codev_platform.reindex import cgroup_v2
from codev_platform.reindex.cgroup_process import (
    CgroupAttemptProcessBackend,
    CgroupDelegationError,
    SystemdCgroupV2,
    encode_cgroup_native_ref,
    parse_cgroup_events,
)
from codev_platform.reindex.cgroup_v2 import _systemd_delegate_enabled

from tests import reindex_cgroup_process_support as support

@pytest.mark.parametrize(
    ("name", "allowed"),
    [
        (
            "posix_bootstrap.py",
            {
                "__future__",
                "collections",
                "ctypes",
                "os",
                "pathlib",
                "signal",
                "sys",
            },
        ),
        (
            "cgroup_bootstrap.py",
            {
                "__future__",
                "collections",
                "ctypes",
                "os",
                "pathlib",
                "re",
                "signal",
                "sys",
            },
        ),
    ],
)
def test_bootstrap_import_surface_contains_only_standard_library(
    name: str,
    allowed: set[str],
) -> None:
    path = Path(__file__).parents[1] / "codev_platform" / "reindex" / name
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            if node.module:
                imported.add(node.module.split(".", 1)[0])

    assert imported <= allowed
    assert not any(
        isinstance(node, ast.Name) and node.id == "__import__" for node in ast.walk(tree)
    )
    assert "codev_platform" not in source
    assert "linux_parent_guard" not in source

@pytest.mark.parametrize("poll_interval", [float("nan"), float("inf")])
def test_cgroup_rejects_nonfinite_poll_interval(
    tmp_path: Path,
    poll_interval: float,
) -> None:
    with pytest.raises(ValueError, match="poll_interval"):
        CgroupAttemptProcessBackend(
            filesystem=support._FakeCgroupFs(tmp_path),
            process_table=support._FakeProcessTable(),
            poll_interval=poll_interval,
        )


def test_cgroup_invalid_wall_clock_is_rejected_before_group_creation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fs = support._FakeCgroupFs(tmp_path)
    monkeypatch.setattr(
        fs,
        "create_attempt",
        lambda _attempt_id: (_ for _ in ()).throw(AssertionError("不得创建 cgroup")),
    )
    backend = CgroupAttemptProcessBackend(
        filesystem=fs,
        process_table=support._FakeProcessTable(),
        wall_clock=lambda: float("nan"),
    )

    with pytest.raises(ValueError, match="时钟"):
        backend.prepare(
            attempt_id="invalid-clock",
            argv=[sys.executable, "-c", "pass"],
            cwd=tmp_path,
            bootstrap_log=tmp_path / "invalid-clock.log",
            deadline=Deadline.start(1.0),
        )


def test_cgroup_rejects_noncallable_runtime_dependency(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="必须可调用"):
        CgroupAttemptProcessBackend(
            filesystem=support._FakeCgroupFs(tmp_path),
            process_table=support._FakeProcessTable(),
            sleeper=None,
        )


def test_cgroup_readiness_delegates_same_deadline_to_filesystem(tmp_path: Path) -> None:
    fs = support._FakeCgroupFs(tmp_path)
    clock = support._FakeClock()
    deadline = Deadline(clock.monotonic_value + 1.0)

    support._backend(fs, support._FakeProcessTable(), clock).assert_ready(deadline)

    assert fs.readiness_deadlines == [deadline]


def test_cgroup_readiness_maps_delegation_ambiguity_to_backend_error(
    tmp_path: Path,
) -> None:
    fs = support._FakeCgroupFs(tmp_path)
    fs.readiness_failure = CgroupDelegationError("cgroup.kill 不可写")

    with pytest.raises(ProcessBackendReadinessError, match="cgroup.*readiness"):
        support._backend(fs, support._FakeProcessTable(), support._FakeClock()).assert_ready(Deadline.start(1.0))


def test_cgroup_readiness_preserves_memory_error(tmp_path: Path) -> None:
    fs = support._FakeCgroupFs(tmp_path)
    fs.readiness_failure = MemoryError("内存不足")

    with pytest.raises(MemoryError, match="内存不足"):
        support._backend(fs, support._FakeProcessTable(), support._FakeClock()).assert_ready(Deadline.start(1.0))


def test_cgroup_readiness_expired_budget_does_not_touch_filesystem(tmp_path: Path) -> None:
    fs = support._FakeCgroupFs(tmp_path)
    clock = support._FakeClock()

    with pytest.raises(ProcessBackendReadinessError, match="预算"):
        support._backend(fs, support._FakeProcessTable(), clock).assert_ready(
            Deadline(clock.monotonic_value)
        )

    assert fs.readiness_deadlines == []


def test_cgroup_readiness_deadline_crossing_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fs = support._FakeCgroupFs(tmp_path)
    clock = support._FakeClock()

    def _cross_deadline(deadline: Deadline) -> None:
        fs.readiness_deadlines.append(deadline)
        clock.sleep(2.0)

    monkeypatch.setattr(fs, "assert_ready", _cross_deadline)

    with pytest.raises(ProcessBackendReadinessError, match="预算"):
        support._backend(fs, support._FakeProcessTable(), clock).assert_ready(
            Deadline(clock.monotonic_value + 1.0)
        )

    assert len(fs.readiness_deadlines) == 1


def test_cgroup_events_accepts_only_exact_populated_zero() -> None:
    assert parse_cgroup_events(b"populated 0\nfrozen 0\n") is False
    assert parse_cgroup_events(b"populated 1\nfrozen 0\n") is True
    with pytest.raises(ValueError):
        parse_cgroup_events(b"frozen 0\n")
    with pytest.raises(ValueError):
        parse_cgroup_events(b"populated 0\npopulated 1\n")
    with pytest.raises(ValueError):
        parse_cgroup_events(b"populated yes\n")


@pytest.mark.parametrize(
    ("membership", "uses_user_manager"),
    [
        ("/system.slice/codev-reindex.service", False),
        (
            "/user.slice/user-1000.slice/user@1000.service/app.slice/codev-reindex-test.service",
            True,
        ),
    ],
)
def test_systemd_delegation_query_uses_own_service_manager(
    monkeypatch,
    membership: str,
    uses_user_manager: bool,
) -> None:
    commands: list[list[str]] = []

    def _run(command, **_kwargs):
        commands.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout=b"yes\n", stderr=b"")

    monkeypatch.setattr(cgroup_v2.os, "access", lambda *_args: True)
    monkeypatch.setattr(cgroup_v2, "run_tree", _run)

    assert _systemd_delegate_enabled(membership) is True
    assert PurePosixPath(commands[0][0]).is_absolute()
    assert ("--user" in commands[0]) is uses_user_manager
    assert "codev-reindex" in " ".join(commands[0])


def test_systemd_delegation_query_stays_within_shared_deadline(monkeypatch) -> None:
    observed: dict[str, float] = {}

    def _run(_command, **kwargs):
        observed["timeout"] = kwargs["timeout"]
        observed["cleanup"] = kwargs["cleanup_timeout_sec"]
        return subprocess.CompletedProcess(_command, 0, stdout=b"yes\n", stderr=b"")

    monkeypatch.setattr(cgroup_v2.os, "access", lambda *_args: True)
    monkeypatch.setattr(cgroup_v2, "run_tree", _run)
    deadline = Deadline.start(0.5)

    assert _systemd_delegate_enabled(
        "/system.slice/codev-reindex.service",
        deadline,
    ) is True
    assert 0 < observed["timeout"]
    assert 0 < observed["cleanup"]
    assert observed["timeout"] + observed["cleanup"] <= 0.5


def test_systemd_delegation_query_fails_closed_before_expired_budget(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cgroup_v2.os, "access", lambda *_args: True)
    monkeypatch.setattr(
        cgroup_v2,
        "run_tree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("预算耗尽后不得启动 systemctl")
        ),
    )

    assert _systemd_delegate_enabled(
        "/system.slice/codev-reindex.service",
        Deadline.start(0.0),
    ) is False


def test_systemd_cgroup_readiness_reuses_delegation_and_cleans_probe_child(
    tmp_path: Path,
    monkeypatch,
) -> None:
    filesystem = object.__new__(SystemdCgroupV2)
    filesystem.root = tmp_path
    filesystem._membership = "/system.slice/codev-reindex.service"
    events: list[str] = []

    monkeypatch.setattr(
        filesystem,
        "_validate_delegation",
        lambda deadline: events.append(f"delegation:{id(deadline)}"),
    )

    def _verify(child: Path, deadline: Deadline) -> None:
        assert child.parent == tmp_path
        assert child.name.startswith("reindex-probe-")
        events.append(f"kill:{id(deadline)}")

    monkeypatch.setattr(filesystem, "_verify_child", _verify)
    deadline = Deadline.start(1.0)

    filesystem.assert_ready(deadline)

    assert events == [f"delegation:{id(deadline)}", f"kill:{id(deadline)}"]
    assert tuple(tmp_path.iterdir()) == ()


def test_systemd_cgroup_readiness_cleanup_ambiguity_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    filesystem = object.__new__(SystemdCgroupV2)
    filesystem.root = tmp_path
    filesystem._membership = "/system.slice/codev-reindex.service"
    original_rmdir = Path.rmdir
    monkeypatch.setattr(filesystem, "_validate_delegation", lambda _deadline: None)
    monkeypatch.setattr(filesystem, "_verify_child", lambda _child, _deadline: None)

    def _deny_probe_cleanup(path: Path) -> None:
        if path.name.startswith("reindex-probe-"):
            raise PermissionError("拒绝清理")
        original_rmdir(path)

    monkeypatch.setattr(Path, "rmdir", _deny_probe_cleanup)

    with pytest.raises(CgroupDelegationError, match="无法清理"):
        filesystem.assert_ready(Deadline.start(1.0))


def test_systemd_cgroup_readiness_expired_budget_creates_nothing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    filesystem = object.__new__(SystemdCgroupV2)
    filesystem.root = tmp_path
    filesystem._membership = "/system.slice/codev-reindex.service"
    monkeypatch.setattr(
        filesystem,
        "_validate_delegation",
        lambda _deadline: (_ for _ in ()).throw(
            AssertionError("过期预算不得开始 delegation 校验")
        ),
    )

    with pytest.raises(CgroupDelegationError, match="预算"):
        filesystem.assert_ready(Deadline.start(0.0))

    assert tuple(tmp_path.iterdir()) == ()


def test_systemd_cgroup_readiness_opens_kill_control_write_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    child = tmp_path / "reindex-probe-test"
    opened: list[tuple[Path, int]] = []
    closed: list[int] = []

    def _read(path: Path, _limit: int = 0) -> bytes:
        if path.name == "cgroup.type":
            return b"domain\n"
        if path.name == "cgroup.events":
            return b"populated 0\n"
        raise AssertionError(f"不得读取 {path}")

    monkeypatch.setattr(cgroup_v2, "_read_limited", _read)
    monkeypatch.setattr(
        cgroup_v2.os,
        "open",
        lambda path, flags: (opened.append((Path(path), flags)) or 301),
    )
    monkeypatch.setattr(cgroup_v2.os, "close", closed.append)

    SystemdCgroupV2._verify_child(child, Deadline.start(1.0))

    assert opened[0][0] == child / "cgroup.kill"
    assert opened[0][1] & os.O_WRONLY
    assert closed == [301]


def test_systemd_delegate_xattr_cannot_replace_manager_proof(
    tmp_path: Path,
    monkeypatch,
) -> None:
    for name, content in (
        ("cgroup.controllers", b"cpu\n"),
        ("cgroup.events", b"populated 1\n"),
        ("cgroup.procs", b"1\n"),
        ("cgroup.type", b"domain\n"),
    ):
        (tmp_path / name).write_bytes(content)
    filesystem = object.__new__(SystemdCgroupV2)
    filesystem.root = tmp_path
    filesystem._membership = "/system.slice/codev-reindex.service"
    monkeypatch.setattr(
        cgroup_v2.os,
        "getxattr",
        lambda *_args: b"1",
        raising=False,
    )
    monkeypatch.setattr(cgroup_v2.os, "access", lambda *_args: True)
    monkeypatch.setattr(cgroup_v2, "_systemd_delegate_enabled", lambda _path: False)

    with pytest.raises(CgroupDelegationError, match="delegation"):
        filesystem._validate_delegation()


def test_cgroup_exists_attempt_propagates_permission_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    filesystem = object.__new__(SystemdCgroupV2)
    filesystem.root = tmp_path

    class _DeniedPath:
        @staticmethod
        def stat():
            raise PermissionError("denied")

    monkeypatch.setattr(
        filesystem,
        "path_for_attempt",
        lambda _attempt_id: _DeniedPath(),
    )

    with pytest.raises(PermissionError):
        filesystem.exists_attempt("permission-failure")


def test_cgroup_native_ref_never_embeds_attempt_path() -> None:
    native_ref = encode_cgroup_native_ref(support._BOOT_ID, "../../outside/仓库")

    assert "outside" not in native_ref
    assert ".." not in native_ref
    assert "/" not in native_ref
