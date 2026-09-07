"""reindex 变更派发、worker 启动与前台执行策略回归。"""

from __future__ import annotations

import pytest

from tests.reindex_ingest_stage_support import (
    R,
    _固定_dispatch目标提交,
    _预置普通ingest测试的Linux门禁,
)

_共享夹具 = (_固定_dispatch目标提交, _预置普通ingest测试的Linux门禁)


def test_dispatch_enqueues_ingest_on_code_change(tmp_path, monkeypatch):
    enqueued: list[tuple[str, str]] = []

    class _Queue:
        def enqueue(self, project_id, kind, meta=None):
            enqueued.append((project_id, kind))

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **_kwargs: _Queue())
    monkeypatch.setattr(R.C, "project_id_of", lambda _repo: "demo-proj")
    monkeypatch.setattr(R.C, "meta_health", lambda _project_id: {})

    rc = R._dispatch_reindex(
        tmp_path,
        ["apps/web/src/Foo.java"],
        foreground=False,
        trigger_line="t",
        banner="test",
    )

    assert rc == 0
    kinds = {kind for _project_id, kind in enqueued}
    assert "codegraph" in kinds
    assert "ingest" in kinds


def test_dispatch_enqueues_code_vec_on_code_change(tmp_path, monkeypatch):
    enqueued: list[tuple[str, str]] = []

    class _Queue:
        def enqueue(self, project_id, kind, meta=None):
            enqueued.append((project_id, kind))

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **_kwargs: _Queue())
    monkeypatch.setattr(R.C, "project_id_of", lambda _repo: "demo-proj")
    monkeypatch.setattr(R.C, "meta_health", lambda _project_id: {})

    rc = R._dispatch_reindex(
        tmp_path,
        ["apps/web/src/Foo.java"],
        foreground=False,
        trigger_line="t",
        banner="test",
    )

    assert rc == 0
    kinds = [kind for _project_id, kind in enqueued]
    assert "code_vec" in kinds
    assert kinds.index("code_vec") > kinds.index("codegraph")


def test_dispatch_auto_starts_file_worker(tmp_path, monkeypatch):
    from codev_platform.reindex.queue import FileSpoolQueue

    queue = FileSpoolQueue(tmp_path / "spool")
    calls = {}
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **_kwargs: queue)
    monkeypatch.setattr(R.C, "project_id_of", lambda _repo: "demo-proj")
    monkeypatch.setattr(R.C, "config", lambda: {})
    monkeypatch.setattr(R.C, "meta_health", lambda _project_id: {})
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.ensure_worker_running",
        lambda config, cwd=None, queue=None: (
            calls.update(config=config, cwd=cwd, queue=queue) or {"action": "spawned", "pid": 123}
        ),
    )

    rc = R._dispatch_reindex(
        tmp_path,
        ["apps/web/src/Foo.java"],
        foreground=False,
        trigger_line="t",
        banner="test",
    )

    assert rc == 0
    assert calls["cwd"] == tmp_path
    assert calls["queue"] is queue
    log = (tmp_path / "tools" / "chroma" / "reindex.log").read_text(encoding="utf-8")
    assert "worker auto-start: spawned pid=123" in log


def test_dispatch_auto_start_failure_is_fail_soft(tmp_path, monkeypatch, capsys):
    from codev_platform.reindex.queue import FileSpoolQueue

    queue = FileSpoolQueue(tmp_path / "spool")
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **_kwargs: queue)
    monkeypatch.setattr(R.C, "project_id_of", lambda _repo: "demo-proj")
    monkeypatch.setattr(R.C, "config", lambda: {})
    monkeypatch.setattr(R.C, "meta_health", lambda _project_id: {})

    def boom(_config, cwd=None, queue=None):
        del cwd, queue
        raise RuntimeError("spawn failed")

    monkeypatch.setattr("codev_platform.reindex.supervisor.ensure_worker_running", boom)

    rc = R._dispatch_reindex(
        tmp_path,
        ["apps/web/src/Foo.java"],
        foreground=False,
        trigger_line="t",
        banner="test",
    )

    assert rc == 0
    log = (tmp_path / "tools" / "chroma" / "reindex.log").read_text(encoding="utf-8")
    assert "worker auto-start failed" in log
    assert "worker auto-start failed" in capsys.readouterr().err


def test_dispatch_auto_start_fail_action_is_fail_soft(tmp_path, monkeypatch, capsys):
    from codev_platform.reindex.queue import FileSpoolQueue

    queue = FileSpoolQueue(tmp_path / "spool")
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **_kwargs: queue)
    monkeypatch.setattr(R.C, "project_id_of", lambda _repo: "demo-proj")
    monkeypatch.setattr(R.C, "config", lambda: {})
    monkeypatch.setattr(R.C, "meta_health", lambda _project_id: {})
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.ensure_worker_running",
        lambda _config, cwd=None, queue=None: {
            "action": "fail",
            "error": "python not found",
        },
    )

    rc = R._dispatch_reindex(
        tmp_path,
        ["apps/web/src/Foo.java"],
        foreground=False,
        trigger_line="t",
        banner="test",
    )

    assert rc == 0
    log = (tmp_path / "tools" / "chroma" / "reindex.log").read_text(encoding="utf-8")
    assert "worker auto-start: fail python not found" in log
    assert "worker auto-start failed: python not found" in capsys.readouterr().err


def test_dispatch_does_not_auto_start_pg_queue_by_default(tmp_path, monkeypatch):
    enqueued: list[tuple[str, str]] = []

    class _PostgresLikeQueue:
        def enqueue(self, project_id, kind, meta=None):
            enqueued.append((project_id, kind))

    monkeypatch.setattr(
        "codev_platform.reindex.open_default_queue",
        lambda **_kwargs: _PostgresLikeQueue(),
    )
    monkeypatch.setattr(R.C, "project_id_of", lambda _repo: "demo-proj")
    monkeypatch.setattr(R.C, "config", lambda: {})
    monkeypatch.setattr(R.C, "meta_health", lambda _project_id: {})

    def should_not_start(_config, cwd=None, queue=None):
        del cwd, queue
        raise AssertionError("PG-like queue must not auto-start by default")

    monkeypatch.setattr("codev_platform.reindex.supervisor.ensure_worker_running", should_not_start)

    rc = R._dispatch_reindex(
        tmp_path,
        ["apps/web/src/Foo.java"],
        foreground=False,
        trigger_line="t",
        banner="test",
    )

    assert rc == 0
    assert enqueued
    log = (tmp_path / "tools" / "chroma" / "reindex.log").read_text(encoding="utf-8")
    assert "worker auto-start" not in log


def test_dispatch_foreground_skips_drain_when_worker_running(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path / "data"))
    enqueued: list[tuple[str, str]] = []

    class _Queue:
        def enqueue(self, project_id, kind, meta=None):
            enqueued.append((project_id, kind))

    class _Worker:
        def __init__(self, *args, **kwargs):
            raise AssertionError("foreground drain must not run while worker lock is held")

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **_kwargs: _Queue())
    monkeypatch.setattr("codev_platform.reindex.ReindexWorker", _Worker)
    monkeypatch.setattr(R.C, "project_id_of", lambda _repo: "demo-proj")
    monkeypatch.setattr(R.C, "config", lambda: {})
    monkeypatch.setattr(R.C, "meta_health", lambda _project_id: {})

    from codev_platform.reindex import supervisor

    with supervisor.acquire_run_lock(supervisor.new_owner_token()) as acquired:
        assert acquired is True
        rc = R._dispatch_reindex(
            tmp_path,
            ["apps/web/src/Foo.java"],
            foreground=True,
            trigger_line="t",
            banner="test",
        )

    assert rc == 0
    assert enqueued
    assert "跳过 foreground drain" in capsys.readouterr().out


def test_dispatch_foreground_defaults_to_isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path / "data"))
    calls: list[str] = []

    class _Queue:
        def enqueue(self, _project_id, _kind, meta=None):
            assert meta.target_commit == "e" * 40

    class _Loop:
        def drain_once(self):
            calls.append("isolated-drain")
            return 1

    class _Runtime:
        loop = _Loop()

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **_kwargs: _Queue())
    monkeypatch.setattr(
        "codev_platform.reindex.ReindexWorker",
        lambda *_args, **_kwargs: pytest.fail("默认 foreground 不得构造 legacy worker"),
    )
    monkeypatch.setattr(
        "codev_platform.reindex.isolated_worker_composer.build_isolated_worker",
        lambda _config, _owner: calls.append("build") or _Runtime(),
    )
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: {})
    monkeypatch.setattr(R.C, "project_id_of", lambda _repo: "demo-proj")
    monkeypatch.setattr(R.C, "config", lambda: {})
    monkeypatch.setattr(R.C, "meta_health", lambda _project_id: {})

    assert (
        R._dispatch_reindex(
            tmp_path,
            ["apps/web/src/Foo.java"],
            foreground=True,
            trigger_line="t",
            banner="test",
        )
        == 0
    )
    assert calls == ["build", "isolated-drain"]

    from codev_platform.reindex import supervisor

    assert supervisor.worker_status()["execution_mode"] == "isolated"


def test_dispatch_enqueues_parent_project_for_extra_repo_change(tmp_path, monkeypatch):
    enqueued: list[tuple[str, str]] = []

    class _Queue:
        def enqueue(self, project_id, kind, meta=None):
            enqueued.append((project_id, kind))

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **_kwargs: _Queue())
    monkeypatch.setattr(R.C, "project_id_of", lambda _repo: "child-proj")
    monkeypatch.setattr(R.C, "config", lambda: {})
    monkeypatch.setattr(R.C, "meta_health", lambda _project_id: {})
    monkeypatch.setattr(
        "codev_platform.ops.reindex.dispatch.impacted_project_ids_for_repo",
        lambda _repo, **_kwargs: ["child-proj", "parent-proj"],
    )

    rc = R._dispatch_reindex(
        tmp_path,
        ["apps/web/src/Foo.java"],
        foreground=False,
        trigger_line="t",
        banner="test",
    )

    assert rc == 0
    assert [project_id for project_id, kind in enqueued if kind == "codegraph"] == [
        "child-proj",
        "parent-proj",
    ]
    parent_order = [kind for project_id, kind in enqueued if project_id == "parent-proj"]
    assert parent_order == ["codegraph", "ingest", "code_vec"]
