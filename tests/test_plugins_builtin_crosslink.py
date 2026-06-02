"""builtin.cross_link 内置插件测试 — 注册可见 / 缺库返空 / 有库返非空.

不触真实平台 data/: 用 PLATFORM_DATA_DIR 指向 tmp, 控制 cross-link sqlite 的有无。
- 缺库: tmp 下不建 sqlite -> analyze 返空 AnalyzerResult (不抛)。
- 有库: 在 cross_link_db_path 处建最小 nodes/edges sqlite -> analyze 返非空。
注册可见性测试走真实 _discover_builtins (clear_registry 后 list 触发重新发现)。
"""
from __future__ import annotations

import sqlite3

import pytest

from codev_platform.graph.adapters.cross_link import PLUGIN_NAME
from codev_platform.graph.schema import AnalyzerResult
from codev_platform.plugins import clear_registry, get_plugin, registered_names
from codev_platform.plugins.builtin.cross_link import CrossLinkPlugin


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


# ---- 注册可见性 ----

def test_builtin_crosslink_registered():
    # list/registered_names 触发 _ensure_discovered -> _discover_builtins
    assert PLUGIN_NAME in registered_names()
    plugin = get_plugin(PLUGIN_NAME)
    assert plugin.name == "builtin.cross_link"
    assert plugin.version == "0.1.0"


def test_detect_always_true(tmp_path):
    assert CrossLinkPlugin().detect(tmp_path) is True


# ---- analyze: 缺库返空 ----

def test_analyze_missing_db_returns_empty(tmp_path, monkeypatch):
    # 把 data_root 指向空 tmp -> cross-link sqlite 不存在 -> 返空, 不抛
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    result = CrossLinkPlugin().analyze(tmp_path, "demo-proj")
    assert isinstance(result, AnalyzerResult)
    assert result.nodes == [] and result.edges == []
    assert result.plugin == PLUGIN_NAME


# ---- analyze: 有库返非空 ----

def _build_min_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE nodes (
                id INTEGER PRIMARY KEY,
                kind TEXT, name TEXT, path TEXT, line INTEGER,
                language TEXT, meta_json TEXT
            );
            CREATE TABLE edges (
                src_id INTEGER, rel TEXT, dst_id INTEGER,
                confidence REAL, evidence TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO nodes(id, kind, name, path, line, language, meta_json) "
            "VALUES (1, 'table', 'stock_quote_daily', NULL, NULL, 'sql', NULL)"
        )
        conn.execute(
            "INSERT INTO nodes(id, kind, name, path, line, language, meta_json) "
            "VALUES (2, 'java_method', 'QuoteRepo.read', 'Repo.java', 10, 'java', NULL)"
        )
        conn.execute(
            "INSERT INTO edges(src_id, rel, dst_id, confidence, evidence) "
            "VALUES (2, 'reads_table', 1, 0.9, 'select * from stock_quote_daily')"
        )
        conn.commit()
    finally:
        conn.close()


def test_analyze_with_db_returns_nonempty(tmp_path, monkeypatch):
    from codev_platform.core.paths import cross_link_db_path

    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    _build_min_db(cross_link_db_path("demo-proj"))

    result = CrossLinkPlugin().analyze(tmp_path, "demo-proj")
    assert isinstance(result, AnalyzerResult)
    assert len(result.nodes) == 2
    assert len(result.edges) == 1
    assert result.plugin == PLUGIN_NAME
    names = {n.name for n in result.nodes}
    assert "stock_quote_daily" in names
