"""健康检查必须跟随 platform-docs 的原子切换指针。"""
from __future__ import annotations

import json

from codev_platform.core.index_handoff import commit_build
from codev_platform.ops.health._checks import _check_chroma_data
from codev_platform.ops.health._util import Report


def _manifest(chunk_count: int) -> dict:
    return {
        "version": 2,
        "params": {
            "manifest_version": 2,
            "embed_model": "bge-m3",
            "embed_dim": 1024,
            "embed_max_seq_length": 512,
            "chunk_target_max": 800,
            "chunk_hard_max": 1200,
        },
        "files": {
            "docs/a.md": {
                "sha256": "a" * 64,
                "chunk_count": chunk_count,
            },
        },
    }


def _collection_row(report: Report) -> dict:
    return next(row for row in report.rows if row.get("tag") == "chroma collection")


def test_light_health_reads_current_build_manifest_instead_of_stale_base_stamp(tmp_path):
    root = tmp_path / "chroma"
    base = root / "docs" / "proj"
    active = base / "builds" / "active"
    active.mkdir(parents=True)
    (active / "index_manifest.proj.json").write_text(
        json.dumps(_manifest(7)), encoding="utf-8",
    )
    (base / ".last_build.json").write_text(
        json.dumps({"chunks": 999, "embed_dim": 384, "embed_model": "old"}),
        encoding="utf-8",
    )
    commit_build(base, "active")

    report = Report()
    _check_chroma_data(report, True, None, root, tmp_path, "proj")

    row = _collection_row(report)
    assert row["status"] == "OK"
    assert "chunks=7" in row["msg"]
    assert "dim=1024" in row["msg"]
    assert "chunks=999" not in row["msg"]


def test_full_health_opens_current_build_instead_of_project_base(monkeypatch, tmp_path):
    root = tmp_path / "chroma"
    base = root / "docs" / "proj"
    active = base / "builds" / "active"
    active.mkdir(parents=True)
    (active / "chroma.sqlite3").touch()
    commit_build(base, "active")
    python = tmp_path / "python"
    python.touch()
    observed: dict[str, str] = {}

    def fake_run_py(_python, code, timeout):
        observed["code"] = code
        return 0, "chunks=7 dim=1024"

    monkeypatch.setattr("codev_platform.ops.health._checks._run_py", fake_run_py)

    report = Report()
    _check_chroma_data(report, False, python, root, tmp_path, "proj")

    assert f"PersistentClient(path=r'''{active}''')" in observed["code"]
    assert _collection_row(report)["status"] == "OK"


def test_light_health_legacy_layout_prefers_valid_stamp_over_old_manifest(tmp_path):
    root = tmp_path / "chroma"
    base = root / "docs" / "proj"
    base.mkdir(parents=True)
    (base / "index_manifest.proj.json").write_text(
        json.dumps({"version": 1, "params": {}, "files": {}}), encoding="utf-8",
    )
    (base / ".last_build.json").write_text(
        json.dumps({"chunks": 5, "embed_dim": 384, "embed_model": "legacy"}),
        encoding="utf-8",
    )

    report = Report()
    _check_chroma_data(report, True, None, root, tmp_path, "proj")

    row = _collection_row(report)
    assert row["status"] == "OK"
    assert "chunks=5" in row["msg"]


def test_full_health_does_not_open_chroma_when_resolved_database_is_missing(monkeypatch, tmp_path):
    root = tmp_path / "chroma"
    base = root / "docs" / "proj"
    base.mkdir(parents=True)
    python = tmp_path / "python"
    python.touch()

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("数据库不存在时不应启动 Chroma 探针")

    monkeypatch.setattr("codev_platform.ops.health._checks._run_py", fail_if_called)

    report = Report()
    _check_chroma_data(report, False, python, root, tmp_path, "proj")

    row = _collection_row(report)
    assert row["status"] == "FAIL"
    assert "chroma.sqlite3 missing" in row["msg"]
    assert not (base / "chroma.sqlite3").exists()
