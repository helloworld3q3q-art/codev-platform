"""Project id resolver migration coverage."""
from __future__ import annotations

import json

from codev_platform.core import project_id


def test_resolve_local_prefers_codex_project_json(monkeypatch, tmp_path):
    monkeypatch.delenv(project_id.ENV_VAR, raising=False)
    monkeypatch.delenv(project_id.CONFIG_PATHS_ENV_VAR, raising=False)
    monkeypatch.setenv("CODEV_PLATFORM_CONFIG", str(tmp_path / "missing-config.json"))
    claude = tmp_path / ".claude" / "project.json"
    claude.parent.mkdir(parents=True)
    claude.write_text(json.dumps({"project_id": "legacy-proj"}), encoding="utf-8")
    codex = tmp_path / ".codex" / "project.json"
    codex.parent.mkdir(parents=True)
    codex.write_text(json.dumps({"project_id": "codex-proj"}), encoding="utf-8")

    assert project_id.resolve_local(tmp_path) == "codex-proj"


def test_resolve_local_falls_back_to_claude_project_json(monkeypatch, tmp_path):
    monkeypatch.delenv(project_id.ENV_VAR, raising=False)
    monkeypatch.delenv(project_id.CONFIG_PATHS_ENV_VAR, raising=False)
    monkeypatch.setenv("CODEV_PLATFORM_CONFIG", str(tmp_path / "missing-config.json"))
    claude = tmp_path / ".claude" / "project.json"
    claude.parent.mkdir(parents=True)
    claude.write_text(json.dumps({"project_id": "legacy-proj"}), encoding="utf-8")

    assert project_id.resolve_local(tmp_path) == "legacy-proj"


def test_resolve_local_order_is_configurable(monkeypatch, tmp_path):
    monkeypatch.delenv(project_id.ENV_VAR, raising=False)
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps({
            "project": {
                "project_config_paths": [".claude/project.json", ".codex/project.json"],
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEV_PLATFORM_CONFIG", str(cfg))

    claude = tmp_path / ".claude" / "project.json"
    claude.parent.mkdir(parents=True)
    claude.write_text(json.dumps({"project_id": "legacy-proj"}), encoding="utf-8")
    codex = tmp_path / ".codex" / "project.json"
    codex.parent.mkdir(parents=True)
    codex.write_text(json.dumps({"project_id": "codex-proj"}), encoding="utf-8")

    assert project_id.resolve_local(tmp_path) == "legacy-proj"


def test_resolve_local_order_env_overrides_config(monkeypatch, tmp_path):
    monkeypatch.delenv(project_id.ENV_VAR, raising=False)
    monkeypatch.setenv("CODEV_PLATFORM_CONFIG", str(tmp_path / "missing-config.json"))
    monkeypatch.setenv(
        project_id.CONFIG_PATHS_ENV_VAR,
        ".claude/project.json;.codex/project.json",
    )

    claude = tmp_path / ".claude" / "project.json"
    claude.parent.mkdir(parents=True)
    claude.write_text(json.dumps({"project_id": "legacy-proj"}), encoding="utf-8")
    codex = tmp_path / ".codex" / "project.json"
    codex.parent.mkdir(parents=True)
    codex.write_text(json.dumps({"project_id": "codex-proj"}), encoding="utf-8")

    assert project_id.resolve_local(tmp_path) == "legacy-proj"
