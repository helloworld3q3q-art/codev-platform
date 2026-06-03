"""A3 退役 cross-link 自动重建 —— 回归测试。

parity 达标后 (store 每项覆盖 ≥ cross_layer), cross_link/cross_layer 不再**自动**重建:
- auto_reindex_kinds 从 classify_scopes 结果滤掉 cross_link
- classify_scopes 本身仍诚实分类 (.sql/.xml 确与 cross-link 相关, 供 dirty 显示 / 手动)
- 手动 `reindex --cross-link` + runner 不受影响 (本测试不碰那条路径)
"""
from __future__ import annotations

from codev_platform.ops.reindex import auto_reindex_kinds, classify_scopes


def test_auto_reindex_drops_cross_link():
    scoped = {"chroma": ["a.md"], "cross_link": ["b.sql"], "codegraph": ["c.py"]}
    kinds = auto_reindex_kinds(scoped)
    assert "cross_link" not in kinds
    assert set(kinds) == {"chroma", "codegraph"}


def test_auto_reindex_only_cross_link_is_empty():
    # 仅命中退役 scope → 自动入队为空 (上层据此 no-op, 不触发 cross_layer 重建)
    assert auto_reindex_kinds({"cross_link": ["b.sql"]}) == []


def test_auto_reindex_preserves_order_and_other_kinds():
    assert auto_reindex_kinds({"codegraph": ["x.py"], "chroma": ["y.md"]}) == ["codegraph", "chroma"]


def test_classify_scopes_still_buckets_cross_link():
    # 分类层不变 (诚实): .sql 仍归 cross_link, 供 dirty 显示 / 手动重建参考。
    pats = {"doc": [r"\.md$"], "cross_link": [r"\.sql$"], "codegraph": [r"\.py$"]}
    scoped = classify_scopes(["a.sql", "b.py"], pats)
    assert scoped.get("cross_link") == ["a.sql"]
    # 但自动入队滤掉它, 只留 codegraph
    assert auto_reindex_kinds(scoped) == ["codegraph"]
