"""ops.logs 纯定位 + tail 单测 —— log_sources 用包定位, tail_file 用 tmp 文件验。"""
from __future__ import annotations

from codev_platform.ops import logs as L


def test_log_sources_keys_and_data_root_located():
    src = L.log_sources()
    assert {"chroma", "codegraph", "audit"} <= set(src)
    # 落 data_root/logs, 文件名带 <prefix>_ 前缀防多 daemon 同名碰撞
    assert src["chroma"].name == "chroma_mcp_server.log"
    assert src["codegraph"].name == "codegraph_mcp_server.log"
    # 两者同一 logs 目录, 靠文件名前缀区分 (不再靠包目录)
    assert src["chroma"].parent == src["codegraph"].parent
    assert src["chroma"].parent.name == "logs"


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
