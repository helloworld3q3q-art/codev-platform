"""ops.logs 纯定位 + tail 单测 —— log_sources 用包定位, tail_file 用 tmp 文件验。"""
from __future__ import annotations

from codev_platform.ops import logs as L


def test_log_sources_keys_and_package_located():
    src = L.log_sources()
    assert {"chroma", "cross-link", "codegraph", "audit"} <= set(src)
    # 用包定位: chroma/cross-link/codegraph 指向各包目录的 mcp_server.log
    for name in ("chroma", "cross-link", "codegraph"):
        assert src[name].name == "mcp_server.log"
    # 各 MCP server 日志在不同包目录下
    assert src["chroma"].parent != src["cross-link"].parent
    assert src["chroma"].parent != src["codegraph"].parent


def test_tail_file_missing_returns_empty(tmp_path):
    assert L.tail_file(tmp_path / "nope.log", 10) == []


def test_tail_file_returns_last_n(tmp_path):
    p = tmp_path / "x.log"
    p.write_text("\n".join(f"line{i}" for i in range(20)) + "\n", encoding="utf-8")
    assert L.tail_file(p, 3) == ["line17", "line18", "line19"]
    # n >= 文件行数 -> 全量
    assert len(L.tail_file(p, 999)) == 20
    # n <= 0 -> 全量 (不切片)
    assert len(L.tail_file(p, 0)) == 20


def test_serve_log_dir_named_mcp_serve_logs():
    assert L.serve_log_dir().name == "mcp_serve_logs"
