from __future__ import annotations

import time
from datetime import datetime
from types import SimpleNamespace

import pytest

from codev_platform.index_manifest import BuildRecord, record_build
from codev_platform.ops.reindex import commands, wait
from codev_platform.reindex.index_status_client import IndexStatusItem, IndexStatusSnapshot
from codev_platform.reindex.queue import Job, JobMeta
from codev_platform.reindex.worker import ReindexWorker


@pytest.fixture(autouse=True)
def _use_local_manifest_strategy(monkeypatch):
    """Keep unit tests independent from the developer machine's WSL config."""
    monkeypatch.setattr(wait, "wsl_data_owner", lambda _cfg: None)


def test_queued_expected_jobs_parses_worker_log_block():
    lines = [
        "trigger commit: abc",
        "enqueued -> codev-reindex worker: codev-platform -> chroma, codegraph",
        "enqueued -> codev-reindex worker: openclaw-stock -> codegraph",
    ]

    assert commands._queued_expected_jobs(lines, 0, len(lines)) == [
        ("codev-platform", "chroma"),
        ("codev-platform", "codegraph"),
        ("openclaw-stock", "codegraph"),
    ]


def test_reindex_block_end_stops_at_next_hook_block():
    lines = [
        "===== reindex started at 2026-07-07 10:00:00 =====",
        "trigger commit: abc",
        "enqueued -> codev-reindex worker: demo -> chroma",
        "===== reindex started at 2026-07-07 10:00:01 =====",
        "trigger merge/pull: ORIG_HEAD..HEAD",
        "reindex finished at 2026-07-07 10:00:02 [ok]",
    ]

    assert commands._reindex_block_end(lines, 1) == 3


def test_latest_trigger_index_uses_last_matching_commit():
    lines = [
        "trigger commit: old",
        "trigger commit: abc",
        "trigger commit: other",
        "trigger commit: abc",
    ]

    assert commands._latest_trigger_index(lines, "trigger commit: abc") == 3


def test_trigger_started_at_uses_latest_matching_hook_block():
    lines = [
        "===== reindex started at 2026-08-01 10:00:00 =====",
        "trigger epoch: 100.125000",
        "trigger commit: abc",
        "===== reindex started at 2026-08-01 11:00:00 =====",
        "trigger epoch: 200.750000",
        "trigger commit: abc",
    ]

    assert wait._trigger_started_at(lines, "trigger commit: abc") == 200.75


def test_trigger_started_at_keeps_legacy_timestamp_compatibility():
    lines = [
        "===== reindex started at 2026-08-01 11:00:00 =====",
        "trigger commit: abc",
    ]

    expected = datetime.strptime("2026-08-01 11:00:00", "%Y-%m-%d %H:%M:%S").timestamp()
    assert wait._trigger_started_at(lines, "trigger commit: abc") == expected


def test_remote_owner_timeout_guidance_never_suggests_direct_queue_or_duplicate_enqueue():
    guidance = "\n".join(wait._timeout_guidance(remote_owner=True))

    assert "Web control plane" in guidance
    assert "codev-web.service" in guidance
    assert "19xxx MCP" in guidance
    assert "reindex-queue status" not in guidance
    assert "git hook run post-commit" not in guidance


def test_queued_jobs_completed_uses_manifest_and_empty_queue(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    commit = "a" * 40
    record_build(BuildRecord(project_id="demo", kind="chroma",
                             git_commit=commit, status="ok"))

    class _Queue:
        def peek(self):
            return []

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda: _Queue())

    assert commands._queued_jobs_completed(tmp_path, [("demo", "chroma")], commit) == (True, "ok")


def test_queued_jobs_completed_manifest_error_falls_back(monkeypatch, tmp_path):
    commit = "a" * 40

    class _Queue:
        def peek(self):
            return []

    def boom(project_id):
        raise RuntimeError("manifest locked")

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda: _Queue())
    monkeypatch.setattr("codev_platform.index_manifest.read_manifest", boom)

    assert commands._queued_jobs_completed(tmp_path, [("demo", "chroma")], commit) == (False, "")


def test_queued_jobs_completed_uses_manifest_when_windows_cannot_peek_wsl_queue(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    commit = "9" * 40
    record_build(BuildRecord(project_id="demo", kind="chroma", git_commit=commit, status="ok"))

    class _Queue:
        def peek(self):
            raise OSError(5, "UNC locking unavailable")

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda: _Queue())

    assert commands._queued_jobs_completed(tmp_path, [("demo", "chroma")], commit) == (True, "ok")


def test_completed_status_uses_expected_manifest_jobs_without_local_trigger(monkeypatch, tmp_path):
    commit = "8" * 40
    monkeypatch.setattr(
        wait,
        "_queued_jobs_completed",
        lambda repo, expected, got_commit: (
            expected == [("demo", "chroma")] and got_commit == commit,
            "ok",
        ),
    )

    assert wait._completed_status(
        tmp_path,
        ["reindex enqueue failed (ignored): stage=open-queue OSError errno=5"],
        f"trigger commit: {commit}",
        commit,
        expected_jobs=[("demo", "chroma")],
    ) == ("reindex manifest covers", "ok")


def test_completed_status_keeps_planned_jobs_when_log_contains_partial_enqueue(tmp_path):
    commit = "6" * 40
    seen: list[tuple[str, str]] = []

    def probe(_repo, expected, _commit):
        seen.extend(expected)
        return False, "pending"

    result = wait._completed_status(
        tmp_path,
        [
            f"trigger commit: {commit}",
            "enqueued -> codev-reindex worker: child -> chroma",
            "reindex finished at 2026-08-01 10:00:00 [ok]",
        ],
        f"trigger commit: {commit}",
        commit,
        expected_jobs=[("parent", "chroma"), ("child", "chroma")],
        completion_probe=probe,
        allow_legacy_fallback=False,
    )

    assert result is None
    assert seen == [("parent", "chroma"), ("child", "chroma")]


def test_completed_status_remote_manifest_pending_rejects_legacy_success(tmp_path):
    commit = "5" * 40

    assert wait._completed_status(
        tmp_path,
        [f"trigger commit: {commit}", "reindex finished at now [ok]"],
        f"trigger commit: {commit}",
        commit,
        expected_jobs=[("demo", "chroma")],
        completion_probe=lambda *_args: (False, "chroma:stale"),
        allow_legacy_fallback=False,
    ) is None


def test_http_jobs_completed_never_opens_windows_local_queue_or_manifest(monkeypatch, tmp_path):
    commit = "7" * 40
    snapshot = IndexStatusSnapshot(
        head_commit=commit,
        items=tuple(
            IndexStatusItem(
                kind=kind,
                status="ok",
                git_commit=commit,
                fresh=True,
                reason="aligned",
            )
            for kind in ("codegraph", "ingest", "code_vec")
        ),
    )
    calls = []

    monkeypatch.setattr(
        wait,
        "read_platform_index_status",
        lambda project_id, cfg: calls.append(project_id) or snapshot,
    )
    monkeypatch.setattr(
        "codev_platform.reindex.open_default_queue",
        lambda: (_ for _ in ()).throw(AssertionError("local queue must not open")),
    )
    monkeypatch.setattr(
        "codev_platform.index_manifest.read_manifest",
        lambda _pid: (_ for _ in ()).throw(AssertionError("local manifest must not open")),
    )

    assert wait._http_jobs_completed(
        tmp_path,
        [("demo", "codegraph"), ("demo", "ingest"), ("demo", "code_vec")],
        commit,
        cfg={},
    ) == (True, "ok")
    assert calls == ["demo"]


def test_wait_command_remote_owner_routes_completion_through_http_only(
    monkeypatch,
    tmp_path,
    capsys,
):
    commit = "4" * 40
    snapshot = IndexStatusSnapshot(
        head_commit=commit,
        items=(IndexStatusItem(
            kind="chroma",
            status="ok",
            git_commit=commit,
            fresh=True,
            reason="aligned",
        ),),
    )
    monkeypatch.setattr(wait, "wsl_data_owner", lambda _cfg: object())
    monkeypatch.setattr(commands.C, "resolve_repo", lambda _repo: tmp_path)
    monkeypatch.setattr(commands.C, "config", lambda: {})
    monkeypatch.setattr(wait, "_reindex_log", lambda _repo: tmp_path / "missing.log")
    monkeypatch.setattr(wait, "expected_reindex_jobs", lambda _repo, _changed: [("demo", "chroma")])
    monkeypatch.setattr(wait, "read_platform_index_status", lambda _pid, _cfg: snapshot)
    monkeypatch.setattr(
        wait,
        "_queued_jobs_completed",
        lambda *_args: (_ for _ in ()).throw(AssertionError("local completion forbidden")),
    )

    def fake_git_out(_repo, *args):
        if args[:1] == ("rev-parse",):
            return 0, commit
        if args[:1] == ("diff-tree",):
            return 0, "docs/changed.md"
        return 0, ""

    monkeypatch.setattr(wait, "_git_out", fake_git_out)

    rc = commands.cmd_wait_for_reindex(SimpleNamespace(
        repo=str(tmp_path),
        commit=commit,
        timeout_sec=1,
    ))

    assert rc == 0
    assert "manifest covers" in capsys.readouterr().out


def test_http_jobs_completed_returns_terminal_failure(monkeypatch, tmp_path):
    commit = "6" * 40
    snapshot = IndexStatusSnapshot(
        head_commit=commit,
        items=(
            IndexStatusItem(
                kind="code_vec",
                status="failed",
                git_commit=commit,
                fresh=False,
                reason="build failed",
            ),
        ),
    )
    monkeypatch.setattr(wait, "read_platform_index_status", lambda _project_id, _cfg: snapshot)

    assert wait._http_jobs_completed(
        tmp_path,
        [("demo", "code_vec")],
        commit,
        cfg={},
    ) == (True, "failed:code_vec:failed")


def test_queued_jobs_completed_accepts_descendant_manifest_commit(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    target = "a" * 40
    indexed = "b" * 40
    record_build(BuildRecord(project_id="demo", kind="chroma",
                             git_commit=indexed, status="ok"))

    class _Queue:
        def peek(self):
            return []

    def fake_git_out(repo, *args):
        if args == ("merge-base", "--is-ancestor", target, indexed):
            return 0, ""
        return 1, ""

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda: _Queue())
    monkeypatch.setattr(wait, "_git_out", fake_git_out)

    assert commands._queued_jobs_completed(tmp_path, [("demo", "chroma")], target) == (True, "ok")


def test_queued_jobs_completed_rejects_unrelated_manifest_commit(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    target = "a" * 40
    indexed = "b" * 40
    record_build(BuildRecord(project_id="demo", kind="chroma",
                             git_commit=indexed, status="ok"))

    class _Queue:
        def peek(self):
            return []

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda: _Queue())
    monkeypatch.setattr(wait, "_git_out", lambda repo, *args: (1, ""))

    assert commands._queued_jobs_completed(tmp_path, [("demo", "chroma")], target) == (False, "")


def test_queued_jobs_completed_fails_when_dependency_failed(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    commit = "a" * 40
    record_build(BuildRecord(project_id="demo", kind="codegraph", git_commit=commit, status="failed"))
    record_build(BuildRecord(
        project_id="demo",
        kind="code_vec",
        git_commit=commit,
        target_commit=commit,
        status="ok",
        depends_json='[{"kind":"codegraph","status":"failed","git_commit":"' + commit + '","target_commit":"' + commit + '"}]',
    ))

    class _Queue:
        def peek(self):
            return []

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda: _Queue())

    assert commands._queued_jobs_completed(tmp_path, [("demo", "code_vec")], commit) == (
        True,
        "failed:code_vec:dependency:codegraph:failed",
    )


def test_queued_jobs_completed_waits_when_dependency_not_covered(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    target = "a" * 40
    indexed = "b" * 40
    record_build(BuildRecord(project_id="demo", kind="codegraph", git_commit=indexed, status="ok"))
    record_build(BuildRecord(
        project_id="demo",
        kind="code_vec",
        git_commit=target,
        target_commit=target,
        status="ok",
        depends_json='[{"kind":"codegraph","status":"ok","git_commit":"' + indexed + '","target_commit":"' + target + '"}]',
    ))

    class _Queue:
        def peek(self):
            return []

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda: _Queue())
    monkeypatch.setattr(wait, "_git_out", lambda repo, *args: (1, ""))

    assert commands._queued_jobs_completed(tmp_path, [("demo", "code_vec")], target) == (False, "")


def test_queued_jobs_completed_fails_when_dependency_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    commit = "a" * 40
    record_build(BuildRecord(
        project_id="demo",
        kind="code_vec",
        git_commit=commit,
        target_commit=commit,
        status="ok",
        depends_json='[{"kind":"codegraph","status":"missing","target_commit":"' + commit + '"}]',
    ))

    class _Queue:
        def peek(self):
            return []

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda: _Queue())

    assert commands._queued_jobs_completed(tmp_path, [("demo", "code_vec")], commit) == (
        True,
        "failed:code_vec:dependency:codegraph:missing",
    )


def test_queued_jobs_completed_fails_when_dependency_metadata_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    commit = "c" * 40
    record_build(BuildRecord(
        project_id="demo",
        kind="code_vec",
        git_commit=commit,
        target_commit=commit,
        status="ok",
    ))

    class _Queue:
        def peek(self):
            return []

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda: _Queue())

    assert commands._queued_jobs_completed(tmp_path, [("demo", "code_vec")], commit) == (
        True,
        "failed:code_vec:dependency:codegraph:metadata-missing",
    )


def test_wait_for_reindex_accepts_worker_manifest_completion(monkeypatch, tmp_path, capsys):
    commit = "b" * 40
    short = commit[:7]
    log_file = tmp_path / "reindex.log"
    log_file.write_text(
        "\n".join([
            "===== reindex started at 2026-07-07 10:00:00 =====",
            f"trigger commit: {commit}",
            "projects:       demo",
            "scopes:         chroma",
            "matched paths:",
            "README.md",
            "enqueued -> codev-reindex worker: demo -> chroma",
        ]),
        encoding="utf-8",
    )

    monkeypatch.setattr(wait, "_reindex_log", lambda repo: log_file)
    monkeypatch.setattr(commands.C, "resolve_repo", lambda repo: tmp_path)
    monkeypatch.setattr(commands.C, "project_id_of", lambda repo: "demo")
    monkeypatch.setattr(commands.C, "meta_health", lambda pid: {})
    monkeypatch.setattr(commands.C, "reindex_patterns",
                        lambda meta: {"doc": [r"\.md$"], "codegraph": []})

    def fake_git_out(repo, *args):
        if args == ("rev-parse", short):
            return 0, commit
        if args[:1] == ("diff-tree",):
            return 0, "README.md"
        return 0, commit

    monkeypatch.setattr(wait, "_git_out", fake_git_out)
    monkeypatch.setattr(wait, "_queued_jobs_completed",
                        lambda repo, expected, got_commit: (
                            expected == [("demo", "chroma")] and got_commit == commit, "ok"))

    rc = commands.cmd_wait_for_reindex(SimpleNamespace(
        repo=str(tmp_path),
        commit=short,
        timeout_sec=1,
    ))

    assert rc == 0
    assert f"[OK] reindex manifest covers {short} status=ok" in capsys.readouterr().out


def test_wait_for_reindex_fails_when_manifest_reports_dependency_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path / "data"))
    commit = "2" * 40
    short = commit[:7]
    log_file = tmp_path / "reindex.log"
    log_file.write_text(
        "\n".join([
            "===== reindex started at 2026-07-07 10:00:00 =====",
            f"trigger commit: {commit}",
            "projects:       demo",
            "scopes:         code_vec",
            "matched paths:",
            "codev_platform/x.py",
            "enqueued -> codev-reindex worker: demo -> code_vec",
        ]),
        encoding="utf-8",
    )

    monkeypatch.setattr(wait, "_reindex_log", lambda repo: log_file)
    monkeypatch.setattr(commands.C, "resolve_repo", lambda repo: tmp_path)
    monkeypatch.setattr(commands.C, "project_id_of", lambda repo: "demo")
    monkeypatch.setattr(commands.C, "meta_health", lambda pid: {"reindex_codegraph_patterns": [r"\.py$"]})
    monkeypatch.setattr(commands.C, "reindex_patterns",
                        lambda meta: {"doc": [], "codegraph": [r"\.py$"]})

    record_build(BuildRecord(
        project_id="demo",
        kind="code_vec",
        git_commit=commit,
        target_commit=commit,
        status="ok",
        depends_json='[{"kind":"codegraph","status":"missing","target_commit":"' + commit + '"}]',
    ))
    for kind in ("codegraph", "ingest"):
        record_build(BuildRecord(
            project_id="demo",
            kind=kind,
            git_commit=commit,
            target_commit=commit,
            status="ok",
        ))

    def fake_git_out(repo, *args):
        if args == ("rev-parse", short):
            return 0, commit
        if args[:1] == ("diff-tree",):
            return 0, "codev_platform/x.py"
        return 0, commit

    class _Queue:
        def peek(self):
            return []

    monkeypatch.setattr(wait, "_git_out", fake_git_out)
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda: _Queue())

    rc = commands.cmd_wait_for_reindex(SimpleNamespace(
        repo=str(tmp_path),
        commit=short,
        timeout_sec=1,
    ))

    assert rc == 1
    assert "dependency:codegraph:missing" in capsys.readouterr().out


def test_wait_for_reindex_uses_latest_block_when_same_commit_reruns(
    monkeypatch,
    tmp_path,
    capsys,
):
    commit = "e" * 40
    short = commit[:7]
    log_file = tmp_path / "reindex.log"
    log_file.write_text(
        "\n".join([
            "===== reindex started at 2026-07-07 10:00:00 =====",
            f"trigger commit: {commit}",
            "reindex finished at 2026-07-07 10:00:02 [ok]",
            "===== reindex started at 2026-07-07 10:05:00 =====",
            f"trigger commit: {commit}",
            "projects:       demo",
            "scopes:         code_vec",
            "matched paths:",
            "codev_platform/x.py",
            "enqueued -> codev-reindex worker: demo -> code_vec",
        ]),
        encoding="utf-8",
    )

    monkeypatch.setattr(wait, "_reindex_log", lambda repo: log_file)
    monkeypatch.setattr(commands.C, "resolve_repo", lambda repo: tmp_path)
    monkeypatch.setattr(commands.C, "project_id_of", lambda repo: "demo")
    monkeypatch.setattr(commands.C, "meta_health", lambda pid: {"reindex_codegraph_patterns": [r"\.py$"]})
    monkeypatch.setattr(commands.C, "reindex_patterns",
                        lambda meta: {"doc": [], "codegraph": [r"\.py$"]})

    def fake_git_out(repo, *args):
        if args == ("rev-parse", short):
            return 0, commit
        if args[:1] == ("diff-tree",):
            return 0, "codev_platform/x.py"
        return 0, commit

    monkeypatch.setattr(wait, "_git_out", fake_git_out)
    monkeypatch.setattr(wait, "_queued_jobs_completed",
                        lambda repo, expected, got_commit: (True, "failed"))

    rc = commands.cmd_wait_for_reindex(SimpleNamespace(
        repo=str(tmp_path),
        commit=short,
        timeout_sec=1,
    ))

    assert rc == 1
    out = capsys.readouterr().out
    assert "[FAIL] reindex manifest covers" in out


def test_wait_for_reindex_legacy_completion_is_used_only_when_plan_is_unavailable(
    monkeypatch,
    tmp_path,
    capsys,
):
    commit = "f" * 40
    short = commit[:7]
    log_file = tmp_path / "reindex.log"
    log_file.write_text(
        "\n".join([
            "===== reindex started at 2026-07-07 10:00:00 =====",
            f"trigger commit: {commit}",
            "reindex finished at 2026-07-07 10:00:02 [failed exit=1]",
            "===== reindex started at 2026-07-07 10:05:00 =====",
            f"trigger commit: {commit}",
            "reindex finished at 2026-07-07 10:05:02 [ok]",
        ]),
        encoding="utf-8",
    )

    monkeypatch.setattr(wait, "_reindex_log", lambda repo: log_file)
    monkeypatch.setattr(commands.C, "resolve_repo", lambda repo: tmp_path)
    monkeypatch.setattr(commands.C, "project_id_of", lambda repo: "demo")
    monkeypatch.setattr(commands.C, "meta_health", lambda pid: {})
    monkeypatch.setattr(commands.C, "reindex_patterns",
                        lambda meta: {"doc": [r"\.md$"], "codegraph": []})

    def fake_git_out(repo, *args):
        if args == ("rev-parse", short):
            return 0, commit
        if args[:1] == ("diff-tree",):
            return 1, ""
        return 0, commit

    monkeypatch.setattr(wait, "_git_out", fake_git_out)

    rc = commands.cmd_wait_for_reindex(SimpleNamespace(
        repo=str(tmp_path),
        commit=short,
        timeout_sec=1,
    ))

    assert rc == 0
    assert "[OK] reindex finished" in capsys.readouterr().out


def test_wait_for_reindex_fails_when_worker_manifest_failed(monkeypatch, tmp_path, capsys):
    commit = "c" * 40
    short = commit[:7]
    log_file = tmp_path / "reindex.log"
    log_file.write_text(
        "\n".join([
            "===== reindex started at 2026-07-07 10:00:00 =====",
            f"trigger commit: {commit}",
            "projects:       demo",
            "scopes:         code_vec",
            "matched paths:",
            "codev_platform/x.py",
            "enqueued -> codev-reindex worker: demo -> code_vec",
        ]),
        encoding="utf-8",
    )

    monkeypatch.setattr(wait, "_reindex_log", lambda repo: log_file)
    monkeypatch.setattr(commands.C, "resolve_repo", lambda repo: tmp_path)
    monkeypatch.setattr(commands.C, "project_id_of", lambda repo: "demo")
    monkeypatch.setattr(commands.C, "meta_health", lambda pid: {"reindex_codegraph_patterns": [r"\.py$"]})
    monkeypatch.setattr(commands.C, "reindex_patterns",
                        lambda meta: {"doc": [], "codegraph": [r"\.py$"]})

    def fake_git_out(repo, *args):
        if args == ("rev-parse", short):
            return 0, commit
        if args[:1] == ("diff-tree",):
            return 0, "codev_platform/x.py"
        return 0, commit

    monkeypatch.setattr(wait, "_git_out", fake_git_out)
    monkeypatch.setattr(wait, "_queued_jobs_completed",
                        lambda repo, expected, got_commit: (True, "failed"))

    rc = commands.cmd_wait_for_reindex(SimpleNamespace(
        repo=str(tmp_path),
        commit=short,
        timeout_sec=1,
    ))

    assert rc == 1
    assert f"[FAIL] reindex manifest covers {short} status=failed" in capsys.readouterr().out


def test_wait_for_reindex_fails_when_manifest_reports_dependency_failure(monkeypatch, tmp_path, capsys):
    commit = "1" * 40
    short = commit[:7]
    log_file = tmp_path / "reindex.log"
    log_file.write_text(
        "\n".join([
            "===== reindex started at 2026-07-07 10:00:00 =====",
            f"trigger commit: {commit}",
            "projects:       demo",
            "scopes:         code_vec",
            "matched paths:",
            "codev_platform/x.py",
            "enqueued -> codev-reindex worker: demo -> code_vec",
        ]),
        encoding="utf-8",
    )

    monkeypatch.setattr(wait, "_reindex_log", lambda repo: log_file)
    monkeypatch.setattr(commands.C, "resolve_repo", lambda repo: tmp_path)
    monkeypatch.setattr(commands.C, "project_id_of", lambda repo: "demo")
    monkeypatch.setattr(commands.C, "meta_health", lambda pid: {"reindex_codegraph_patterns": [r"\.py$"]})
    monkeypatch.setattr(commands.C, "reindex_patterns",
                        lambda meta: {"doc": [], "codegraph": [r"\.py$"]})

    def fake_git_out(repo, *args):
        if args == ("rev-parse", short):
            return 0, commit
        if args[:1] == ("diff-tree",):
            return 0, "codev_platform/x.py"
        return 0, commit

    monkeypatch.setattr(wait, "_git_out", fake_git_out)
    monkeypatch.setattr(
        wait,
        "_queued_jobs_completed",
        lambda repo, expected, got_commit: (True, "failed:code_vec:dependency:codegraph:failed"),
    )

    rc = commands.cmd_wait_for_reindex(SimpleNamespace(
        repo=str(tmp_path),
        commit=short,
        timeout_sec=1,
    ))

    assert rc == 1
    assert "dependency:codegraph:failed" in capsys.readouterr().out


def test_wait_for_reindex_legacy_failure_is_used_only_when_plan_is_unavailable(
    monkeypatch,
    tmp_path,
    capsys,
):
    commit = "d" * 40
    short = commit[:7]
    log_file = tmp_path / "reindex.log"
    log_file.write_text(
        "\n".join([
            "===== reindex started at 2026-07-07 10:00:00 =====",
            f"trigger commit: {commit}",
            "reindex finished at 2026-07-07 10:00:02 [failed exit=1]",
        ]),
        encoding="utf-8",
    )

    monkeypatch.setattr(wait, "_reindex_log", lambda repo: log_file)
    monkeypatch.setattr(commands.C, "resolve_repo", lambda repo: tmp_path)
    monkeypatch.setattr(commands.C, "project_id_of", lambda repo: "demo")
    monkeypatch.setattr(commands.C, "meta_health", lambda pid: {})
    monkeypatch.setattr(commands.C, "reindex_patterns",
                        lambda meta: {"doc": [r"\.md$"], "codegraph": []})

    def fake_git_out(repo, *args):
        if args == ("rev-parse", short):
            return 0, commit
        if args[:1] == ("diff-tree",):
            return 1, ""
        return 0, commit

    monkeypatch.setattr(wait, "_git_out", fake_git_out)

    rc = commands.cmd_wait_for_reindex(SimpleNamespace(
        repo=str(tmp_path),
        commit=short,
        timeout_sec=1,
    ))

    assert rc == 1
    assert "[FAIL] reindex finished" in capsys.readouterr().out


def test_wait_for_reindex_legacy_warning_is_not_reported_as_success(
    monkeypatch,
    tmp_path,
    capsys,
):
    commit = "e" * 40
    short = commit[:7]
    log_file = tmp_path / "reindex.log"
    log_file.write_text(
        "\n".join([
            "===== reindex started at 2026-07-07 10:00:00 =====",
            f"trigger commit: {commit}",
            "reindex finished at 2026-07-07 10:00:02 [warn exit=2]",
        ]),
        encoding="utf-8",
    )

    monkeypatch.setattr(wait, "_reindex_log", lambda repo: log_file)
    monkeypatch.setattr(commands.C, "resolve_repo", lambda repo: tmp_path)

    def fake_git_out(repo, *args):
        if args == ("rev-parse", short):
            return 0, commit
        if args[:1] == ("diff-tree",):
            return 1, ""
        return 0, commit

    monkeypatch.setattr(wait, "_git_out", fake_git_out)

    rc = commands.cmd_wait_for_reindex(SimpleNamespace(
        repo=str(tmp_path),
        commit=short,
        timeout_sec=1,
    ))

    assert rc == 1
    out = capsys.readouterr().out
    assert "[FAIL] reindex finished" in out
    assert "warn exit=2" in out
    assert "[OK]" not in out


def test_worker_record_manifest_writes_code_vec_dependency_metadata(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    records: list[BuildRecord] = []
    worker = ReindexWorker(SimpleNamespace(), {})
    job = Job(
        "demo",
        "code_vec",
        time.time(),
        meta=JobMeta(source="webhook", pull_policy="ff_only", target_commit="abc123"),
    )

    monkeypatch.setattr("codev_platform.index_manifest.git_head", lambda repo_path: "abc123")
    monkeypatch.setattr(
        "codev_platform.index_manifest.latest_build",
        lambda project_id, kind: BuildRecord(
            project_id="demo",
            kind="codegraph",
            git_commit="abc123",
            target_commit="abc123",
            status="ok",
        ),
    )
    monkeypatch.setattr("codev_platform.index_manifest.record_build", lambda rec: records.append(rec))

    ok = worker._record_manifest(job, repo, started=10.0, status="ok", note="")

    assert ok is True
    assert len(records) == 1
    rec = records[0]
    assert rec.target_commit == "abc123"
    assert rec.source == "webhook"
    assert rec.pull_policy == "ff_only"
    assert rec.repo_commits_json == '{"main":"abc123"}'
    assert rec.depends_json == '[{"kind":"codegraph","status":"ok","git_commit":"abc123","target_commit":"abc123"}]'
