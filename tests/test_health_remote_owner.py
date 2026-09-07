from __future__ import annotations

from pathlib import Path

from codev_platform.core.wsl_data_owner import WslDataOwner
from codev_platform.ops import health
from codev_platform.ops.health._remote import RemoteHealthContext, report_remote_stack
from codev_platform.ops.health._util import Report


def test_report_remote_stack_uses_http_project_snapshot():
    report = Report()
    context = RemoteHealthContext(
        owner=WslDataOwner("Ubuntu", "/srv/codev/data"),
        project_id="demo",
        project={
            "chroma_chunks": 12,
            "codegraph": {"nodes": 20, "edges": 30},
            "graph": {"nodes": 4, "edges": 5},
            "usage_7d": {"search_docs": 6, "codegraph": 7},
        },
        endpoints=(
            {"name": "platform-docs", "status": "ok", "port": 19083},
            {"name": "codegraph", "status": "ok", "port": 19091},
            {"name": "agent-memory", "status": "ok", "port": 19087},
            {"name": "graph", "status": "ok", "port": 19092},
        ),
    )

    report_remote_stack(report, context)

    rows = {row["tag"]: row for row in report.rows}
    assert report.red == 0
    assert rows["platform data owner"]["status"] == "OK"
    assert "via HTTP" in rows["platform data owner"]["msg"]
    assert "chunks=12" in rows["chroma collection"]["msg"]
    assert "nodes=20 edges=30" in rows["codegraph db"]["msg"]
    assert "nodes=4 edges=5" in rows["graph store"]["msg"]
    assert rows["MCP endpoints"]["status"] == "OK"
    assert "search_docs=6" in rows["platform usage 7d"]["msg"]


def test_report_remote_stack_fails_when_required_endpoint_is_missing():
    report = Report()
    context = RemoteHealthContext(
        owner=WslDataOwner("Ubuntu", "/srv/codev/data"),
        project_id="demo",
        project={"chroma_chunks": 12, "codegraph": {}, "graph": {}},
        endpoints=(
            {"name": "platform-docs", "status": "ok", "port": 19083},
            {"name": "codegraph", "status": "ok", "port": 19091},
            {"name": "graph", "status": "ok", "port": 19092},
        ),
    )

    report_remote_stack(report, context)

    row = next(item for item in report.rows if item["tag"] == "MCP endpoints")
    assert row["status"] == "FAIL"
    assert "agent-memory" in row["msg"]


def test_report_remote_stack_does_not_label_http_failure_as_index_corruption():
    report = Report()
    context = RemoteHealthContext(
        owner=WslDataOwner("Ubuntu", "/srv/codev/data"),
        project_id="demo",
        error_type="URLError",
    )

    report_remote_stack(report, context)

    assert report.red == 1
    message = report.rows[-1]["msg"]
    assert "HTTP unavailable" in message
    assert "serve-mcp status" in message
    assert "corrupt" not in message.lower()


def test_remote_owner_blocks_default_and_explicit_wsl_snapshot_writes(tmp_path):
    owner = WslDataOwner("Ubuntu", "/srv/codev/data")

    assert health._health_snapshot_write_block_reason(
        owner,
        "",
        tmp_path / "default.json",
    )
    assert health._health_snapshot_write_block_reason(
        owner,
        r"\\wsl.localhost\Ubuntu\srv\codev\data\health.json",
        Path(r"\\wsl.localhost\Ubuntu\srv\codev\data\health.json"),
    )
    assert health._health_snapshot_write_block_reason(
        owner,
        str(tmp_path / "local.json"),
        tmp_path / "local.json",
    ) is None
