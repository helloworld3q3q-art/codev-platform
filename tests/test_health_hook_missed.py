from __future__ import annotations

from pathlib import Path

import pytest

from codev_platform.ops.health import _checks
from codev_platform.ops.health._util import Report


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


def test_hook_missed_rejects_codegraph_manifest_even_when_all_expected_kinds_are_fresh(tmp_path, monkeypatch):
    _patch_git(monkeypatch, ["codev_platform/ops/health/_checks.py"])
    _patch_manifest(monkeypatch, [
        {"kind": "codegraph", "status": "ok", "fresh": True},
        {"kind": "ingest", "status": "ok", "fresh": True},
        {"kind": "code_vec", "status": "ok", "fresh": True},
    ])

    report = _run(tmp_path, {"reindex_codegraph_patterns": [r"^codev_platform/.*\.py$"]})

    assert report.amber == 1
    assert report.rows[0]["status"] == "WARN"
    assert "manifest fallback limited to chroma-only" in report.rows[0]["msg"]
    assert "codegraph" in report.rows[0]["msg"]
    assert "ingest" in report.rows[0]["msg"]
    assert "code_vec" in report.rows[0]["msg"]


def test_hook_missed_still_warns_when_expected_manifest_kind_missing(tmp_path, monkeypatch):
    _patch_git(monkeypatch, ["codev_platform/ops/health/_checks.py"])
    _patch_manifest(monkeypatch, [
        {"kind": "codegraph", "status": "ok", "fresh": True},
        {"kind": "code_vec", "status": "ok", "fresh": True},
    ])

    report = _run(tmp_path, {"reindex_codegraph_patterns": [r"^codev_platform/.*\.py$"]})

    assert report.amber == 1
    assert report.rows[0]["status"] == "WARN"
    assert "manifest fallback limited to chroma-only" in report.rows[0]["msg"]


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


def test_hook_missed_keeps_legacy_reindex_log_success(tmp_path, monkeypatch):
    _patch_git(monkeypatch, ["docs/plans/roadmap.md"])
    _patch_manifest(monkeypatch, RuntimeError("should not be called"))
    log = tmp_path / "tools" / "chroma" / "reindex.log"
    log.parent.mkdir(parents=True)
    log.write_text(f"done {HEAD}\n", encoding="utf-8")

    report = _run(tmp_path)

    assert report.amber == 0
    assert report.rows[0]["status"] == "OK"
    assert "found in reindex.log" in report.rows[0]["msg"]


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
