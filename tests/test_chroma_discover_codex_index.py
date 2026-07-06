"""Codex migration coverage for chroma file discovery."""
from __future__ import annotations

import json

import codev_platform.chroma._discover as discover
from codev_platform.chroma._index_config import DOC_PATTERNS


def test_default_doc_patterns_include_codex_rules_and_skills():
    assert ".codex/rules/*.md" in DOC_PATTERNS
    assert ".codex/skills/**/*.md" in DOC_PATTERNS


def test_load_project_index_prefers_codex_index(monkeypatch, tmp_path):
    monkeypatch.setattr(discover, "PLATFORM_ROOT", tmp_path)
    monkeypatch.setattr(discover, "DOC_PATTERNS", ["README.md"])
    monkeypatch.delenv("CODEV_PLATFORM_INDEX_CONFIG_PATHS", raising=False)
    monkeypatch.setenv("CODEV_PLATFORM_CONFIG", str(tmp_path / "missing-config.json"))

    claude = tmp_path / ".claude" / "index.json"
    claude.parent.mkdir(parents=True)
    claude.write_text(json.dumps({"doc_patterns": ["CLAUDE.md"]}), encoding="utf-8")

    codex = tmp_path / ".codex" / "index.json"
    codex.parent.mkdir(parents=True)
    codex.write_text(
        json.dumps({
            "doc_patterns": ["AGENTS.md", ".codex/rules/*.md"],
            "external_doc_paths": ["../shared/*.md"],
        }),
        encoding="utf-8",
    )

    patterns, external = discover._load_project_index_config()
    assert patterns == ["AGENTS.md", ".codex/rules/*.md"]
    assert external == ["../shared/*.md"]


def test_load_project_index_falls_back_to_claude_index(monkeypatch, tmp_path):
    monkeypatch.setattr(discover, "PLATFORM_ROOT", tmp_path)
    monkeypatch.setattr(discover, "DOC_PATTERNS", ["README.md"])
    monkeypatch.delenv("CODEV_PLATFORM_INDEX_CONFIG_PATHS", raising=False)
    monkeypatch.setenv("CODEV_PLATFORM_CONFIG", str(tmp_path / "missing-config.json"))

    claude = tmp_path / ".claude" / "index.json"
    claude.parent.mkdir(parents=True)
    claude.write_text(json.dumps({"doc_patterns": ["CLAUDE.md"]}), encoding="utf-8")

    patterns, external = discover._load_project_index_config()
    assert patterns == ["CLAUDE.md"]
    assert external == []


def test_discover_files_uses_codex_index_patterns(monkeypatch, tmp_path):
    monkeypatch.setattr(discover, "PLATFORM_ROOT", tmp_path)
    monkeypatch.setattr(discover, "DOC_PATTERNS", ["README.md"])
    monkeypatch.delenv("CODEV_PLATFORM_INDEX_CONFIG_PATHS", raising=False)
    monkeypatch.setenv("CODEV_PLATFORM_CONFIG", str(tmp_path / "missing-config.json"))

    codex = tmp_path / ".codex" / "index.json"
    codex.parent.mkdir(parents=True)
    codex.write_text(
        json.dumps({
            "doc_patterns": [
                "AGENTS.md",
                ".codex/rules/*.md",
                "web-ui/AGENTS.md",
                "web-ui/.codex/rules/**/*.md",
            ],
        }),
        encoding="utf-8",
    )

    (tmp_path / "AGENTS.md").write_text("root", encoding="utf-8")
    rules = tmp_path / ".codex" / "rules"
    rules.mkdir(parents=True)
    (rules / "workflow.md").write_text("workflow", encoding="utf-8")
    web_rules = tmp_path / "web-ui" / ".codex" / "rules"
    web_rules.mkdir(parents=True)
    (tmp_path / "web-ui" / "AGENTS.md").write_text("web", encoding="utf-8")
    (web_rules / "code-quality.md").write_text("quality", encoding="utf-8")

    found = {p.relative_to(tmp_path).as_posix() for p in discover.discover_files()}
    assert found == {
        "AGENTS.md",
        ".codex/rules/workflow.md",
        "web-ui/AGENTS.md",
        "web-ui/.codex/rules/code-quality.md",
    }


def test_infer_module_recognizes_web_ui():
    assert discover.infer_module("web-ui/.codex/rules/code-quality.md") == "web-ui"


def test_index_config_order_is_configurable(monkeypatch, tmp_path):
    monkeypatch.setattr(discover, "PLATFORM_ROOT", tmp_path)
    monkeypatch.setattr(discover, "DOC_PATTERNS", ["README.md"])
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps({
            "project": {
                "index_config_paths": [".claude/index.json", ".codex/index.json"],
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEV_PLATFORM_CONFIG", str(cfg))

    claude = tmp_path / ".claude" / "index.json"
    claude.parent.mkdir(parents=True)
    claude.write_text(json.dumps({"doc_patterns": ["CLAUDE.md"]}), encoding="utf-8")
    codex = tmp_path / ".codex" / "index.json"
    codex.parent.mkdir(parents=True)
    codex.write_text(json.dumps({"doc_patterns": ["AGENTS.md"]}), encoding="utf-8")

    patterns, _external = discover._load_project_index_config()
    assert patterns == ["CLAUDE.md"]
