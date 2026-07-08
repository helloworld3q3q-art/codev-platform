from __future__ import annotations

from codev_platform.ops.health._checks import _check_webhook_extra_repo_mapping
from codev_platform.ops.health._util import Report


def test_health_webhook_mapping_skips_project_without_webhook_repo(monkeypatch):
    monkeypatch.setattr(
        "codev_platform.core.repos._read_meta",
        lambda pid: {"extra_repos": ["/abs/child"]} if pid == "oms-work" else {},
    )
    r = Report()

    _check_webhook_extra_repo_mapping(r, {"projects": {"oms-work": {"repo_path": "/abs/oms"}}})

    assert r.rows[0]["tag"] == "webhook extra repos"
    assert r.rows[0]["status"] == "OK"


def test_health_webhook_mapping_warns_for_enabled_parent_extra(tmp_path, monkeypatch):
    child = tmp_path / "child"; child.mkdir()
    monkeypatch.setattr("codev_platform.core.repos._read_meta", lambda pid: {})
    r = Report()

    _check_webhook_extra_repo_mapping(
        r,
        {
            "projects": {
                "parent-proj": {
                    "repo_path": str(tmp_path / "parent"),
                    "webhook_repo": "org/parent",
                    "extra_repos": [str(child)],
                }
            }
        },
    )

    assert r.rows[0]["tag"] == "webhook extra repos"
    assert r.rows[0]["status"] == "WARN"
    assert "parent-proj" in r.rows[0]["msg"]
