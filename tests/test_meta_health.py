"""Regression tests for meta_health() and project_id_of() resilience.

Bug context: these readers must tolerate missing/corrupt JSON and absent files
without crashing the CLI/ops layer -- meta_health returns {} and project_id_of
returns None instead of raising.
"""
from __future__ import annotations

import json

import codev_platform.ops._common as common


# ---------------------------------------------------------------- meta_health
def test_meta_health_reads_health_section(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "codev_root", lambda: tmp_path)
    meta = tmp_path / "platform_meta" / "projects" / "openclaw-stock" / "meta.json"
    meta.parent.mkdir(parents=True)
    meta.write_text(
        json.dumps({"project_id": "openclaw-stock",
                    "health": {"reindex_codegraph_patterns": [r"x\.py$"]}}),
        encoding="utf-8",
    )
    h = common.meta_health("openclaw-stock")
    assert h == {"reindex_codegraph_patterns": [r"x\.py$"]}


def test_meta_health_none_pid_returns_empty():
    assert common.meta_health(None) == {}


def test_meta_health_missing_file_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "codev_root", lambda: tmp_path)
    assert common.meta_health("nonexistent-project") == {}


def test_meta_health_no_health_key_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "codev_root", lambda: tmp_path)
    meta = tmp_path / "platform_meta" / "projects" / "p" / "meta.json"
    meta.parent.mkdir(parents=True)
    meta.write_text(json.dumps({"project_id": "p"}), encoding="utf-8")
    assert common.meta_health("p") == {}


def test_meta_health_corrupt_json_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "codev_root", lambda: tmp_path)
    meta = tmp_path / "platform_meta" / "projects" / "p" / "meta.json"
    meta.parent.mkdir(parents=True)
    meta.write_text("{ not valid json", encoding="utf-8")
    assert common.meta_health("p") == {}


# --------------------------------------------------------------- project_id_of
def test_project_id_of_reads_value(tmp_path):
    pj = tmp_path / ".claude" / "project.json"
    pj.parent.mkdir(parents=True)
    pj.write_text(json.dumps({"project_id": "openclaw-stock"}), encoding="utf-8")
    assert common.project_id_of(tmp_path) == "openclaw-stock"


def test_project_id_of_prefers_codex_value(tmp_path):
    claude = tmp_path / ".claude" / "project.json"
    claude.parent.mkdir(parents=True)
    claude.write_text(json.dumps({"project_id": "legacy"}), encoding="utf-8")
    codex = tmp_path / ".codex" / "project.json"
    codex.parent.mkdir(parents=True)
    codex.write_text(json.dumps({"project_id": "codex"}), encoding="utf-8")
    assert common.project_id_of(tmp_path) == "codex"


def test_project_id_of_falls_back_when_codex_corrupt(tmp_path):
    codex = tmp_path / ".codex" / "project.json"
    codex.parent.mkdir(parents=True)
    codex.write_text("{ broken", encoding="utf-8")
    claude = tmp_path / ".claude" / "project.json"
    claude.parent.mkdir(parents=True)
    claude.write_text(json.dumps({"project_id": "legacy"}), encoding="utf-8")
    assert common.project_id_of(tmp_path) == "legacy"


def test_project_id_of_missing_file_returns_none(tmp_path):
    assert common.project_id_of(tmp_path) is None


def test_project_id_of_corrupt_json_returns_none(tmp_path):
    pj = tmp_path / ".claude" / "project.json"
    pj.parent.mkdir(parents=True)
    pj.write_text("{ broken", encoding="utf-8")
    assert common.project_id_of(tmp_path) is None


def test_project_id_of_no_key_returns_none(tmp_path):
    pj = tmp_path / ".claude" / "project.json"
    pj.parent.mkdir(parents=True)
    pj.write_text(json.dumps({"display_name": "x"}), encoding="utf-8")
    assert common.project_id_of(tmp_path) is None
