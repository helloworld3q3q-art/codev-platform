from __future__ import annotations

from types import SimpleNamespace

from codev_platform.index_manifest import BuildRecord, record_build
from codev_platform.ops.reindex import commands


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
    monkeypatch.setattr(commands, "_git_out", fake_git_out)

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
    monkeypatch.setattr(commands, "_git_out", lambda repo, *args: (1, ""))

    assert commands._queued_jobs_completed(tmp_path, [("demo", "chroma")], target) == (False, "")


def test_wait_for_reindex_accepts_worker_manifest_completion(monkeypatch, tmp_path):
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

    monkeypatch.setattr(commands, "_reindex_log", lambda repo: log_file)
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

    monkeypatch.setattr(commands, "_git_out", fake_git_out)
    monkeypatch.setattr(commands, "_queued_jobs_completed",
                        lambda repo, expected, got_commit: (
                            expected == [("demo", "chroma")] and got_commit == commit, "ok"))

    rc = commands.cmd_wait_for_reindex(SimpleNamespace(
        repo=str(tmp_path),
        commit=short,
        timeout_sec=1,
    ))

    assert rc == 0


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

    monkeypatch.setattr(commands, "_reindex_log", lambda repo: log_file)
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

    monkeypatch.setattr(commands, "_git_out", fake_git_out)
    monkeypatch.setattr(commands, "_queued_jobs_completed",
                        lambda repo, expected, got_commit: (True, "failed"))

    rc = commands.cmd_wait_for_reindex(SimpleNamespace(
        repo=str(tmp_path),
        commit=short,
        timeout_sec=1,
    ))

    assert rc == 1
    out = capsys.readouterr().out
    assert "[FAIL] reindex manifest covers" in out


def test_wait_for_reindex_latest_block_can_clear_old_failure(monkeypatch, tmp_path, capsys):
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

    monkeypatch.setattr(commands, "_reindex_log", lambda repo: log_file)
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

    monkeypatch.setattr(commands, "_git_out", fake_git_out)

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

    monkeypatch.setattr(commands, "_reindex_log", lambda repo: log_file)
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

    monkeypatch.setattr(commands, "_git_out", fake_git_out)
    monkeypatch.setattr(commands, "_queued_jobs_completed",
                        lambda repo, expected, got_commit: (True, "failed"))

    rc = commands.cmd_wait_for_reindex(SimpleNamespace(
        repo=str(tmp_path),
        commit=short,
        timeout_sec=1,
    ))

    assert rc == 1
    assert "[FAIL] reindex manifest covers" in capsys.readouterr().out


def test_wait_for_reindex_fails_when_legacy_finished_failed(monkeypatch, tmp_path, capsys):
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

    monkeypatch.setattr(commands, "_reindex_log", lambda repo: log_file)
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

    monkeypatch.setattr(commands, "_git_out", fake_git_out)

    rc = commands.cmd_wait_for_reindex(SimpleNamespace(
        repo=str(tmp_path),
        commit=short,
        timeout_sec=1,
    ))

    assert rc == 1
    assert "[FAIL] reindex finished" in capsys.readouterr().out
