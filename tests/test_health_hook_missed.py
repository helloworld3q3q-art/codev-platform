from __future__ import annotations

from pathlib import Path

import pytest

from codev_platform.ops.health import _checks
from codev_platform.ops.health._util import Report
from codev_platform.index_manifest import KNOWN_KINDS
from codev_platform.reindex.index_status_client import IndexStatusItem, IndexStatusSnapshot
from codev_platform.reindex.runners import kinds as runner_kinds
from codev_platform.reindex.runners import manifest_covered_kinds


HEAD = "abcdef1234567890"


def _patch_git(monkeypatch, files: list[str], head: str = HEAD) -> None:
    def fake_git(_repo: Path, *args: str) -> tuple[int, str]:
        if args == ("rev-parse", "HEAD"):
            return 0, head
        if args and args[0] == "diff-tree":
            return 0, "\n".join(files)
        return 1, ""

    monkeypatch.setattr(_checks, "_git", fake_git)


def _patch_manifest(monkeypatch, rows: list[dict] | Exception) -> None:
    def fake_freshness(_pid, _repo):
        if isinstance(rows, Exception):
            raise rows
        return rows

    monkeypatch.setattr("codev_platform.index_manifest.freshness", fake_freshness)


def _run(repo: Path, health: dict | None = None,
         project_id: str | None = "codev-platform") -> Report:
    report = Report()
    _checks._check_hook_missed(report, repo, health or {}, project_id)
    return report


def test_hook_missed_accepts_manifest_when_worker_mode_has_no_reindex_log(tmp_path, monkeypatch):
    _patch_git(monkeypatch, ["docs/plans/roadmap.md"])
    _patch_manifest(monkeypatch, [{"kind": "chroma", "status": "ok", "fresh": True}])

    report = _run(tmp_path)

    assert report.amber == 0
    assert report.rows[0]["status"] == "OK"
    assert "covered by manifest (chroma)" in report.rows[0]["msg"]


def test_hook_missed_accepts_full_manifest_for_mixed_doc_and_code_scope(tmp_path, monkeypatch):
    _patch_git(monkeypatch, ["docs/plans/roadmap.md", "codev_platform/ops/health/_checks.py"])
    _patch_manifest(monkeypatch, [
        {"kind": "chroma", "status": "ok", "fresh": True},
        {"kind": "codegraph", "status": "ok", "fresh": True},
        {"kind": "ingest", "status": "ok", "fresh": True},
        {"kind": "code_vec", "status": "ok", "fresh": True},
    ])

    report = _run(tmp_path, {"reindex_codegraph_patterns": [r"^codev_platform/.*\.py$"]})

    assert report.amber == 0
    assert report.rows[0]["status"] == "OK"
    assert "covered by manifest" in report.rows[0]["msg"]
    assert "chroma" in report.rows[0]["msg"]
    assert "codegraph" in report.rows[0]["msg"]
    assert "ingest" in report.rows[0]["msg"]
    assert "code_vec" in report.rows[0]["msg"]


def test_hook_missed_accepts_full_manifest_for_code_scope(tmp_path, monkeypatch):
    _patch_git(monkeypatch, ["codev_platform/ops/health/_checks.py"])
    _patch_manifest(monkeypatch, [
        {"kind": "codegraph", "status": "ok", "fresh": True},
        {"kind": "ingest", "status": "ok", "fresh": True},
        {"kind": "code_vec", "status": "ok", "fresh": True},
    ])

    report = _run(tmp_path, {"reindex_codegraph_patterns": [r"^codev_platform/.*\.py$"]})

    assert report.amber == 0
    assert report.rows[0]["status"] == "OK"
    assert "covered by manifest" in report.rows[0]["msg"]


@pytest.mark.parametrize(
    ("missing", "detail"),
    [
        ("codegraph", "codegraph:missing"),
        ("ingest", "ingest:missing"),
        ("code_vec", "code_vec:missing"),
    ],
)
def test_hook_missed_still_warns_when_expected_manifest_kind_missing(
    tmp_path, monkeypatch, missing, detail,
):
    _patch_git(monkeypatch, ["codev_platform/ops/health/_checks.py"])
    rows = {
        "codegraph": {"kind": "codegraph", "status": "ok", "fresh": True},
        "ingest": {"kind": "ingest", "status": "ok", "fresh": True},
        "code_vec": {"kind": "code_vec", "status": "ok", "fresh": True},
    }
    rows.pop(missing)
    _patch_manifest(monkeypatch, list(rows.values()))

    report = _run(tmp_path, {"reindex_codegraph_patterns": [r"^codev_platform/.*\.py$"]})

    assert report.amber == 1
    assert report.rows[0]["status"] == "WARN"
    assert detail in report.rows[0]["msg"]


def test_hook_missed_reports_manifest_failure_before_success(tmp_path, monkeypatch):
    _patch_git(monkeypatch, ["docs/plans/roadmap.md", "codev_platform/ops/health/_checks.py"])
    _patch_manifest(monkeypatch, [
        {"kind": "chroma", "status": "failed", "fresh": True},
        {"kind": "codegraph", "status": "ok", "fresh": True},
        {"kind": "ingest", "status": "ok", "fresh": True},
        {"kind": "code_vec", "status": "ok", "fresh": True},
    ])

    report = _run(tmp_path, {"reindex_codegraph_patterns": [r"^codev_platform/.*\.py$"]})

    assert report.amber == 1
    assert report.rows[0]["status"] == "WARN"
    assert "chroma:failed" in report.rows[0]["msg"]


@pytest.mark.parametrize(
    ("row", "detail"),
    [
        ({"kind": "code_vec", "status": "failed", "fresh": True}, "code_vec:failed"),
        ({"kind": "codegraph", "status": "ok", "fresh": False}, "codegraph:stale"),
        ({"kind": "ingest", "status": "ok", "fresh": None}, "ingest:stale"),
    ],
)
def test_hook_missed_warns_when_code_manifest_is_not_ok_and_fresh(
    tmp_path, monkeypatch, row, detail,
):
    _patch_git(monkeypatch, ["codev_platform/ops/health/_checks.py"])
    rows = {
        "codegraph": {"kind": "codegraph", "status": "ok", "fresh": True},
        "ingest": {"kind": "ingest", "status": "ok", "fresh": True},
        "code_vec": {"kind": "code_vec", "status": "ok", "fresh": True},
    }
    rows[row["kind"]] = row
    _patch_manifest(monkeypatch, list(rows.values()))

    report = _run(tmp_path, {"reindex_codegraph_patterns": [r"^codev_platform/.*\.py$"]})

    assert report.amber == 1
    assert report.rows[0]["status"] == "WARN"
    assert detail in report.rows[0]["msg"]


@pytest.mark.parametrize(
    ("row", "detail"),
    [
        ({"kind": "chroma", "status": "failed", "fresh": True}, "chroma:failed"),
        ({"kind": "chroma", "status": "ok", "fresh": False}, "chroma:stale"),
        ({"kind": "chroma", "status": "ok", "fresh": None}, "chroma:stale"),
    ],
)
def test_hook_missed_warns_when_chroma_manifest_is_not_ok_and_fresh(
    tmp_path, monkeypatch, row, detail,
):
    _patch_git(monkeypatch, ["docs/plans/roadmap.md"])
    _patch_manifest(monkeypatch, [row])

    report = _run(tmp_path)

    assert report.amber == 1
    assert report.rows[0]["status"] == "WARN"
    assert detail in report.rows[0]["msg"]


def test_hook_missed_warns_when_manifest_unreadable(tmp_path, monkeypatch):
    _patch_git(monkeypatch, ["docs/plans/roadmap.md"])
    _patch_manifest(monkeypatch, RuntimeError("db busy"))

    report = _run(tmp_path)

    assert report.amber == 1
    assert "manifest unreadable" in report.rows[0]["msg"]


def test_hook_missed_warns_when_project_id_unknown(tmp_path, monkeypatch):
    _patch_git(monkeypatch, ["docs/plans/roadmap.md"])
    _patch_manifest(monkeypatch, [{"kind": "chroma", "status": "ok", "fresh": True}])

    report = _run(tmp_path, project_id="unknown")

    assert report.amber == 1
    assert "project_id unknown" in report.rows[0]["msg"]


def test_hook_missed_legacy_success_cannot_override_manifest_failure(tmp_path, monkeypatch):
    _patch_git(monkeypatch, ["docs/plans/roadmap.md"])
    _patch_manifest(monkeypatch, RuntimeError("should not be called"))
    log = tmp_path / "tools" / "chroma" / "reindex.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        "\n".join([
            "===== reindex started at 2026-07-08 18:00:00 =====",
            f"trigger commit: {HEAD}",
            "reindex finished at 2026-07-08 18:00:02 [ok]",
        ]),
        encoding="utf-8",
    )

    report = _run(tmp_path)

    assert report.amber == 1
    assert report.rows[0]["status"] == "WARN"
    assert "cannot override manifest" in report.rows[0]["msg"]


def test_hook_missed_remote_owner_rejects_legacy_success_when_manifest_failed(
    tmp_path,
    monkeypatch,
):
    _patch_git(monkeypatch, ["docs/plans/roadmap.md"])
    log = tmp_path / "tools" / "chroma" / "reindex.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        "\n".join([
            f"trigger commit: {HEAD}",
            "reindex finished at 2026-08-01 10:00:00 [ok]",
        ]),
        encoding="utf-8",
    )
    snapshot = IndexStatusSnapshot(
        head_commit=HEAD,
        items=(IndexStatusItem(
            kind="chroma",
            status="failed",
            git_commit=HEAD,
            fresh=False,
            reason="failed",
        ),),
    )
    monkeypatch.setattr(_checks, "wsl_data_owner", lambda _cfg: object())
    monkeypatch.setattr(
        _checks,
        "read_platform_index_status",
        lambda _project_id, _cfg: snapshot,
    )

    report = Report()
    _checks._check_hook_missed(
        report,
        tmp_path,
        {},
        "codev-platform",
        cfg={"data": {"platform_data_dir": "wsl"}},
    )

    assert report.rows[0]["status"] == "WARN"
    assert "cannot override manifest" in report.rows[0]["msg"]
    assert "chroma:failed" in report.rows[0]["msg"]


def test_hook_missed_accepts_manifest_after_local_enqueue_failure(tmp_path, monkeypatch):
    _patch_git(monkeypatch, ["docs/plans/roadmap.md"])
    _patch_manifest(monkeypatch, [{"kind": "chroma", "status": "ok", "fresh": True}])
    log = tmp_path / "tools" / "chroma" / "reindex.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        "\n".join([
            "===== reindex started at 2026-07-08 18:00:00 =====",
            f"trigger commit: {HEAD}",
            "reindex enqueue failed (ignored): stage=open-queue OSError errno=5",
        ]),
        encoding="utf-8",
    )

    report = _run(tmp_path)

    assert report.amber == 0
    assert "local enqueue failed" in report.rows[0]["msg"]
    assert "covered by manifest" in report.rows[0]["msg"]


def test_hook_missed_warns_with_git_hook_retry_after_enqueue_failure(tmp_path, monkeypatch):
    _patch_git(monkeypatch, ["docs/plans/roadmap.md"])
    _patch_manifest(monkeypatch, [{"kind": "chroma", "status": "ok", "fresh": False}])
    log = tmp_path / "tools" / "chroma" / "reindex.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        "\n".join([
            "===== reindex started at 2026-07-08 18:00:00 =====",
            f"trigger commit: {HEAD}",
            "reindex enqueue failed (ignored): stage=open-queue OSError errno=5",
        ]),
        encoding="utf-8",
    )

    report = _run(tmp_path)

    assert report.amber == 1
    assert "git hook run post-commit" in report.rows[0]["msg"]
    assert "chroma:stale" in report.rows[0]["msg"]


def test_hook_missed_warns_when_queue_log_found_but_manifest_failed(tmp_path, monkeypatch):
    _patch_git(monkeypatch, ["codev_platform/ops/health/_checks.py"])
    _patch_manifest(monkeypatch, [
        {"kind": "codegraph", "status": "ok", "fresh": True},
        {"kind": "ingest", "status": "ok", "fresh": True},
        {"kind": "code_vec", "status": "failed", "fresh": True},
    ])
    log = tmp_path / "tools" / "chroma" / "reindex.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        "\n".join([
            "===== reindex started at 2026-07-08 18:00:00 =====",
            f"trigger commit: {HEAD}",
            "enqueued -> codev-reindex worker: codev-platform -> codegraph, ingest, code_vec",
        ]),
        encoding="utf-8",
    )

    report = _run(tmp_path, {"reindex_codegraph_patterns": [r"^codev_platform/.*\.py$"]})

    assert report.amber == 1
    assert report.rows[0]["status"] == "WARN"
    assert "enqueued in reindex.log but manifest not successful" in report.rows[0]["msg"]
    assert "code_vec:failed" in report.rows[0]["msg"]


def test_hook_missed_warns_when_code_vec_dependency_missing(tmp_path, monkeypatch):
    _patch_git(monkeypatch, ["codev_platform/ops/health/_checks.py"])
    _patch_manifest(monkeypatch, [
        {"kind": "codegraph", "status": "ok", "fresh": True},
        {"kind": "ingest", "status": "ok", "fresh": True},
        {
            "kind": "code_vec",
            "status": "ok",
            "fresh": False,
            "dependency_status": "missing",
            "reason": "code_vec:dependency:codegraph:missing",
        },
    ])

    report = _run(tmp_path, {"reindex_codegraph_patterns": [r"^codev_platform/.*\.py$"]})

    assert report.amber == 1
    assert report.rows[0]["status"] == "WARN"
    assert "code_vec:dependency:codegraph:missing" in report.rows[0]["msg"]


def test_hook_missed_warns_when_code_vec_dependency_stale(tmp_path, monkeypatch):
    _patch_git(monkeypatch, ["codev_platform/ops/health/_checks.py"])
    _patch_manifest(monkeypatch, [
        {"kind": "codegraph", "status": "ok", "fresh": True},
        {"kind": "ingest", "status": "ok", "fresh": True},
        {
            "kind": "code_vec",
            "status": "ok",
            "fresh": False,
            "dependency_status": "stale",
            "reason": "code_vec:dependency:codegraph:stale",
        },
    ])

    report = _run(tmp_path, {"reindex_codegraph_patterns": [r"^codev_platform/.*\.py$"]})

    assert report.amber == 1
    assert report.rows[0]["status"] == "WARN"
    assert "code_vec:dependency:codegraph:stale" in report.rows[0]["msg"]


def test_hook_missed_accepts_code_vec_when_dependency_fresh(tmp_path, monkeypatch):
    _patch_git(monkeypatch, ["codev_platform/ops/health/_checks.py"])
    _patch_manifest(monkeypatch, [
        {"kind": "codegraph", "status": "ok", "fresh": True},
        {"kind": "ingest", "status": "ok", "fresh": True},
        {
            "kind": "code_vec",
            "status": "ok",
            "fresh": True,
            "dependency_status": "ok",
            "reason": "code_vec ok",
        },
    ])

    report = _run(tmp_path, {"reindex_codegraph_patterns": [r"^codev_platform/.*\.py$"]})

    assert report.amber == 0
    assert report.rows[0]["status"] == "OK"
    assert "covered by manifest" in report.rows[0]["msg"]


def test_hook_missed_uses_latest_log_block_for_same_head(tmp_path, monkeypatch):
    _patch_git(monkeypatch, ["codev_platform/ops/health/_checks.py"])
    _patch_manifest(monkeypatch, [
        {"kind": "codegraph", "status": "ok", "fresh": True},
        {"kind": "ingest", "status": "ok", "fresh": True},
        {"kind": "code_vec", "status": "failed", "fresh": True},
    ])
    log = tmp_path / "tools" / "chroma" / "reindex.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        "\n".join([
            f"done legacy {HEAD}",
            "===== reindex started at 2026-07-08 18:00:00 =====",
            f"trigger commit: {HEAD}",
            "enqueued -> codev-reindex worker: codev-platform -> codegraph, ingest, code_vec",
        ]),
        encoding="utf-8",
    )

    report = _run(tmp_path, {"reindex_codegraph_patterns": [r"^codev_platform/.*\.py$"]})

    assert report.amber == 1
    assert "code_vec:failed" in report.rows[0]["msg"]


def test_hook_missed_uses_manifest_when_log_exists_but_lacks_head(tmp_path, monkeypatch):
    _patch_git(monkeypatch, ["docs/plans/roadmap.md"])
    _patch_manifest(monkeypatch, [{"kind": "chroma", "status": "ok", "fresh": True}])
    log = tmp_path / "tools" / "chroma" / "reindex.log"
    log.parent.mkdir(parents=True)
    log.write_text("old commit\n", encoding="utf-8")

    report = _run(tmp_path)

    assert report.amber == 0
    assert report.rows[0]["status"] == "OK"
    assert "reindex.log lacks HEAD" in report.rows[0]["msg"]


def test_hook_missed_uses_full_manifest_when_log_exists_but_lacks_head(tmp_path, monkeypatch):
    _patch_git(monkeypatch, ["codev_platform/ops/health/_checks.py"])
    _patch_manifest(monkeypatch, [
        {"kind": "codegraph", "status": "ok", "fresh": True},
        {"kind": "ingest", "status": "ok", "fresh": True},
        {"kind": "code_vec", "status": "ok", "fresh": True},
    ])
    log = tmp_path / "tools" / "chroma" / "reindex.log"
    log.parent.mkdir(parents=True)
    log.write_text("old commit\n", encoding="utf-8")

    report = _run(tmp_path, {"reindex_codegraph_patterns": [r"^codev_platform/.*\.py$"]})

    assert report.amber == 0
    assert report.rows[0]["status"] == "OK"
    assert "reindex.log lacks HEAD" in report.rows[0]["msg"]


def test_manifest_backed_kinds_track_registered_reindex_kinds():
    assert _checks._MANIFEST_BACKED_KINDS == set(manifest_covered_kinds())
    assert _checks._MANIFEST_BACKED_KINDS <= set(KNOWN_KINDS)
    assert _checks._MANIFEST_BACKED_KINDS <= set(runner_kinds())


def test_manifest_coverage_uses_http_for_windows_wsl_owner(monkeypatch, tmp_path):
    snapshot = IndexStatusSnapshot(
        head_commit=HEAD,
        items=(
            IndexStatusItem(
                kind="code_vec",
                status="failed",
                git_commit=HEAD,
                fresh=False,
                reason="build failed",
            ),
        ),
    )
    monkeypatch.setattr(_checks, "wsl_data_owner", lambda _cfg: object())
    monkeypatch.setattr(
        _checks,
        "read_platform_index_status",
        lambda project_id, cfg: snapshot,
    )
    monkeypatch.setattr(
        "codev_platform.index_manifest.freshness",
        lambda *_args: (_ for _ in ()).throw(AssertionError("local manifest must not open")),
    )

    assert _checks._manifest_head_coverage(
        "demo",
        tmp_path,
        {"code_vec"},
        cfg={"data": {"platform_data_dir": "wsl"}},
        target_commit=HEAD,
    ) == (False, "code_vec:failed")
