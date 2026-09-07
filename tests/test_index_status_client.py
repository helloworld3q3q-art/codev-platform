from __future__ import annotations

import pytest

from codev_platform.reindex import index_status_client as client


def _payload(*items: dict, head: str | None = "a" * 40) -> dict:
    return {
        "result": 0,
        "data": {
            "headCommit": head,
            "items": list(items),
        },
    }


def test_read_platform_index_status_posts_project_scoped_request(monkeypatch):
    seen = {}

    def fake_request(url, cfg, **kwargs):
        seen.update(url=url, cfg=cfg, kwargs=kwargs)
        return _payload(
            {
                "kind": "chroma",
                "status": "ok",
                "gitCommit": "a" * 40,
                "fresh": True,
                "reason": "aligned",
                "finishedAt": 1.5,
                "elapsedSec": 2.5,
            }
        )

    monkeypatch.setattr(client, "authenticated_json_request", fake_request)
    cfg = {"platform": {"web_url": "http://platform.local"}}

    snapshot = client.read_platform_index_status("demo", cfg)

    assert seen == {
        "url": "http://platform.local/api/v1/indexes/status",
        "cfg": cfg,
        "kwargs": {
            "method": "POST",
            "headers": {"X-Project-Id": "demo"},
            "timeout": 10,
            "opener": None,
        },
    }
    assert snapshot.head_commit == "a" * 40
    assert snapshot.by_kind["chroma"].git_commit == "a" * 40
    assert snapshot.by_kind["chroma"].fresh is True


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"result": 1, "data": {}},
        {"result": 0, "data": []},
        {"result": 0, "data": {"items": {}}},
        {"result": 0, "data": {"items": [{"kind": ""}]}},
    ],
)
def test_read_platform_index_status_fails_closed_on_invalid_envelope(monkeypatch, payload):
    monkeypatch.setattr(client, "authenticated_json_request", lambda *_args, **_kwargs: payload)

    with pytest.raises(client.IndexStatusProtocolError):
        client.read_platform_index_status("demo", {})


def test_assess_index_coverage_reports_terminal_index_failure():
    commit = "b" * 40
    snapshot = client.IndexStatusSnapshot(
        head_commit=commit,
        items=(
            client.IndexStatusItem(
                kind="code_vec",
                status="failed",
                git_commit=commit,
                fresh=False,
                reason="build failed",
            ),
        ),
    )

    result = client.assess_index_coverage(
        snapshot,
        {"code_vec"},
        commit,
        commit_covers=lambda target, indexed: target == indexed,
    )

    assert result.state is client.CoverageState.FAILED
    assert result.detail == "code_vec:failed"


def test_assess_index_coverage_waits_when_failure_predates_explicit_retry():
    commit = "e" * 40
    snapshot = client.IndexStatusSnapshot(
        head_commit=commit,
        items=(
            client.IndexStatusItem(
                kind="code_vec",
                status="failed",
                git_commit=commit,
                fresh=False,
                reason="build failed",
                finished_at=10.0,
            ),
        ),
    )

    result = client.assess_index_coverage(
        snapshot,
        {"code_vec"},
        commit,
        commit_covers=lambda target, indexed: target == indexed,
        failure_not_before=20.0,
    )

    assert result.state is client.CoverageState.PENDING
    assert result.detail == "code_vec:retry-pending"


def test_assess_index_coverage_keeps_dependency_stale_pending():
    commit = "c" * 40
    snapshot = client.IndexStatusSnapshot(
        head_commit=commit,
        items=(
            client.IndexStatusItem(
                kind="code_vec",
                status="ok",
                git_commit=commit,
                fresh=False,
                reason="code_vec:dependency:codegraph:stale",
            ),
        ),
    )

    result = client.assess_index_coverage(
        snapshot,
        {"code_vec"},
        commit,
        commit_covers=lambda target, indexed: target == indexed,
    )

    assert result.state is client.CoverageState.PENDING
    assert result.detail == "code_vec:dependency:codegraph:stale"


def test_assess_index_coverage_accepts_all_expected_kinds():
    commit = "d" * 40
    snapshot = client.IndexStatusSnapshot(
        head_commit=commit,
        items=tuple(
            client.IndexStatusItem(
                kind=kind,
                status="ok",
                git_commit=commit,
                fresh=True,
                reason="aligned",
            )
            for kind in ("codegraph", "ingest", "code_vec")
        ),
    )

    result = client.assess_index_coverage(
        snapshot,
        {"codegraph", "ingest", "code_vec"},
        commit,
        commit_covers=lambda target, indexed: target == indexed,
    )

    assert result.state is client.CoverageState.OK
    assert result.detail == "code_vec, codegraph, ingest"
