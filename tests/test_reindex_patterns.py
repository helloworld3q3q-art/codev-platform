"""Regression tests for reindex scope pattern matching.

Bug context: reindex scope must be project-name-free by default so a brand-new
project works with zero meta config. Each project EXTENDS the generic defaults
via meta.health.reindex_*_patterns (single source of truth), it does not replace
them.
"""
from __future__ import annotations

import codev_platform.ops._common as common


# Simulated openclaw-stock meta.health: declares extra codegraph patterns.
OPENCLAW_HEALTH = {
    "reindex_codegraph_patterns": [
        r"python/.*\.py$",
    ],
}


def _scope_of(path: str, health: dict) -> set[str]:
    """Return the set of scopes a path matches."""
    pats = common.reindex_patterns(health)
    return {scope for scope, plist in pats.items() if common.matches_any(path, plist)}


def test_py_matches_codegraph_when_declared():
    assert "codegraph" in _scope_of("python/stock_pipeline/foo.py", OPENCLAW_HEALTH)


def test_docs_md_matches_doc_via_generic_default():
    # no meta needed: docs/*.md is a generic default
    assert "doc" in _scope_of("docs/architecture/plan.md", {})


def test_apps_src_java_matches_codegraph_via_generic_default():
    # zero-config new project: apps/<x>/src/*.java hits generic codegraph default
    assert "codegraph" in _scope_of("apps/whatever/src/Foo.java", {})


def test_apps_src_tsx_matches_codegraph_via_generic_default():
    assert "codegraph" in _scope_of("apps/web/src/pages/Index.tsx", {})


def test_claude_md_matches_doc_via_generic_default():
    assert "doc" in _scope_of("CLAUDE.md", {})


def test_codex_rules_match_doc_via_generic_default():
    assert "doc" in _scope_of(".codex/rules/workflow.md", {})


def test_web_ui_codex_rules_match_doc_via_generic_default():
    assert "doc" in _scope_of("web-ui/.codex/rules/code-quality.md", {})


def test_agents_md_matches_doc_via_generic_default():
    assert "doc" in _scope_of("web-ui/AGENTS.md", {})


def test_random_txt_matches_nothing():
    assert _scope_of("random.txt", OPENCLAW_HEALTH) == set()


def test_random_txt_matches_nothing_empty_health():
    assert _scope_of("notes/random.txt", {}) == set()


def test_defaults_extended_not_replaced():
    # project codegraph extra is APPENDED to the generic default list (not replacing)
    pats = common.reindex_patterns(OPENCLAW_HEALTH)
    assert r"python/.*\.py$" in pats["codegraph"]
    # generic doc/codegraph defaults survive even with project extras present
    for d in common.DEFAULT_DOC_PATTERNS:
        assert d in pats["doc"]
    for d in common.DEFAULT_CODEGRAPH_PATTERNS:
        assert d in pats["codegraph"]


def test_backslash_paths_normalized():
    # Windows-style separators must still match the forward-slash patterns
    assert "doc" in _scope_of(r"docs\architecture\plan.md", {})
