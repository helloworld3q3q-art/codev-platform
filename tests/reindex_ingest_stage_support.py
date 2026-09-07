"""统一图谱 ingest、code_vec 与 dispatch 回归的共享夹具。"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from codev_platform.graph.ingest import IngestFailure, IngestReport
from codev_platform.ops import reindex as R
from tests.reindex_linux_gate import provision_test_gate


@pytest.fixture(autouse=True)
def _固定_dispatch目标提交(monkeypatch):
    """隔离 dispatch 行为测试，避免临时目录承担真实 Git 仓职责。"""
    monkeypatch.setattr(
        "codev_platform.reindex.target_commit.resolve_repo_head",
        lambda _repo: "e" * 40,
    )


@pytest.fixture(autouse=True)
def _预置普通ingest测试的Linux门禁(monkeypatch, tmp_path) -> None:
    """普通 stage 回归不应受真实 WSL 全局门禁是否已部署影响。"""
    provision_test_gate(monkeypatch, tmp_path)


def _args(**kw) -> argparse.Namespace:
    base = {
        "repo": None,
        "chroma": False,
        "codegraph": False,
        "ingest": False,
        "code_vec": False,
        "force": False,
        "proven_runtime": False,
        "expected_project_id": None,
        "expected_runtime_revision": None,
    }
    base.update(kw)
    return argparse.Namespace(**base)


def _report(*, ingested=(), failures=()) -> IngestReport:
    return IngestReport(
        project_id="demo-proj",
        ingested=list(ingested),
        failures=list(failures),
    )


@pytest.fixture(name="_repo")
def _repo_fixture(tmp_path: Path) -> Path:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "project.json").write_text(
        '{"project_id": "demo-proj"}', encoding="utf-8"
    )
    return tmp_path


def _stub_stages(monkeypatch) -> None:
    """让 codegraph/chroma stage 都 no-op 成功, 只留 ingest 真跑。"""

    class _CP:
        returncode = 0

    monkeypatch.setattr(R.C, "run", lambda *args, **kwargs: _CP())
    monkeypatch.setattr(R.C, "chroma_python", lambda: __file__)
    monkeypatch.setattr(
        "codev_platform.recall.code_vector_store.build_code_vector_index",
        lambda project_id, **kwargs: 0,
    )


def _failed_report() -> IngestReport:
    return _report(
        failures=[
            IngestFailure(
                phase="plugin",
                component="fake.required",
                code="ANALYZE_FAILED",
            )
        ]
    )
