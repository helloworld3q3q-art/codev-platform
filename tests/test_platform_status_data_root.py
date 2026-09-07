"""平台状态与运行文件必须共享同一数据根解析契约。"""

from __future__ import annotations

from pathlib import Path
import json

import pytest

from codev_platform import platform_status
import codev_platform.core.runtime_artifacts as runtime_artifacts


def test_环境数据根优先于状态接口配置(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = tmp_path / "配置根"
    selected = tmp_path / "环境根"
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(selected))

    assert (
        platform_status._data_dir({"data": {"platform_data_dir": str(configured)}})
        == selected.resolve()
    )


def test_没有环境覆盖时状态接口使用调用方配置(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "配置根"
    monkeypatch.delenv("PLATFORM_DATA_DIR", raising=False)

    assert (
        platform_status._data_dir({"data": {"platform_data_dir": str(selected)}})
        == selected.resolve()
    )


def test_状态聚合把已解析数据根传入使用率读取而不重载用户配置(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "请求配置根"
    other = tmp_path / "用户配置根"
    log = selected / "logs" / "codegraph_usage.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(
        json.dumps({"project_id": "demo", "ts": "2999-01-01T00:00:00"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("PLATFORM_DATA_DIR", raising=False)
    monkeypatch.setattr(runtime_artifacts, "data_root", lambda: other)
    layout = platform_status._artifact_layout({"data": {"platform_data_dir": str(selected)}})

    usage = platform_status._usage_7d(layout)

    assert usage["demo"]["codegraph"] == 1


def test_mcp使用率公开接口保留旧位置参数兼容(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path / "空数据根"))
    report = platform_status.mcp_usage_report(tmp_path / "legacy-repo")

    assert report["last7d"]["projects"] == []


def test_完整状态聚合把同一数据根传给图谱与软标签(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "请求配置根"
    repo = tmp_path / "repo"
    (repo / "platform_meta" / "projects" / "demo").mkdir(parents=True)
    observed: list[Path] = []
    monkeypatch.delenv("PLATFORM_DATA_DIR", raising=False)
    monkeypatch.setenv("CODEV_PLATFORM_META", str(repo / "platform_meta" / "projects"))
    monkeypatch.setattr(platform_status, "_repo_root", lambda: repo)
    monkeypatch.setattr(
        platform_status,
        "_collect_chroma_doc_chunks",
        lambda _path: ({"demo": 1}, []),
    )
    monkeypatch.setattr(platform_status, "_self_project_id", lambda _repo: None)
    from codev_platform import mcp_source_client

    monkeypatch.setattr(
        mcp_source_client, "probe_source_all", lambda _cfg, _target, **_kwargs: [],
    )
    monkeypatch.setattr(
        platform_status,
        "_local_graph",
        lambda _pid, path: observed.append(path) or "not_built",
    )
    monkeypatch.setattr(
        platform_status,
        "_local_soft_quality",
        lambda _pid, path: observed.append(path) or "not_built",
    )

    platform_status.build_platform_status(
        {"data": {"platform_data_dir": str(selected)}}, platform_docs_ready=True,
    )

    expected = selected.resolve() / "graph_store" / "demo.sqlite"
    assert observed == [expected, expected]


def test_完整状态聚合使用严格http探活而非tcp回退(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CODEV_PLATFORM_META", str(repo / "platform_meta" / "projects"))
    monkeypatch.setattr(platform_status, "_repo_root", lambda: repo)
    monkeypatch.setattr(platform_status, "_collect_chroma_doc_chunks", lambda _path: ({}, []))
    from codev_platform import mcp_source_client

    observed: list[str] = []
    monkeypatch.setattr(
        mcp_source_client,
        "probe_source_all",
        lambda _cfg, target, **kwargs: observed.append((target, kwargs))
        or [
            {"name": "platform-docs", "kind": "chroma", "status": "ok", "reason": ""},
            {"name": "codegraph", "kind": "codegraph", "status": "down"},
        ],
    )

    payload = platform_status.build_platform_status({}, platform_docs_ready=False)

    assert observed == [("local", {"assumed_healthy_kinds": frozenset({"chroma"})})]
    assert payload["mcp_endpoints"] == [
        {
            "name": "platform-docs",
            "kind": "chroma",
            "status": "down",
            "reason": "local source not ready",
        },
        {"name": "codegraph", "kind": "codegraph", "status": "down"},
    ]
