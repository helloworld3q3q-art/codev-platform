"""正式 AI 写入口必须服从 reindex 维护门禁。"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest


@contextmanager
def _denied_permit():
    """模拟已启用 marker 的 fail-closed 写许可。"""
    yield False


def _deny_maintenance_writes(monkeypatch) -> None:
    from codev_platform.reindex import maintenance_gate

    monkeypatch.setattr(
        maintenance_gate,
        "maintenance_reindex_operation_permit",
        lambda: _denied_permit(),
    )


def test_graph_ingest在维护窗口内不得触发图谱写入(monkeypatch, capsys) -> None:
    from codev_platform import cli

    _deny_maintenance_writes(monkeypatch)
    monkeypatch.setattr(
        "codev_platform.graph.ingest.ingest_project",
        lambda *_args, **_kwargs: pytest.fail("维护窗口内不得执行 graph ingest"),
    )

    assert cli.cmd_graph(SimpleNamespace(action="ingest", project="demo", repo=None)) == 1
    assert "维护窗口" in capsys.readouterr().err


def test_chroma模块写入口在维护窗口内不得取得写锁(monkeypatch) -> None:
    from codev_platform.chroma import indexer

    _deny_maintenance_writes(monkeypatch)
    monkeypatch.setattr(indexer, "verify_reindex_runtime_revision", lambda: None)
    monkeypatch.setattr(
        indexer,
        "_try_acquire_reindex_lock",
        lambda: pytest.fail("维护窗口内不得取得 Chroma 写锁"),
    )
    monkeypatch.setattr("sys.argv", ["indexer"])

    assert indexer.main() == 1


def test_code_vec模块写入口在维护窗口内不得构建(monkeypatch) -> None:
    from codev_platform.recall import code_vector_store

    _deny_maintenance_writes(monkeypatch)
    monkeypatch.setattr(
        code_vector_store,
        "build_code_vector_index",
        lambda *_args, **_kwargs: pytest.fail("维护窗口内不得构建 code_vec"),
    )
    monkeypatch.setattr("sys.argv", ["code-vector-store", "--project", "demo"])

    assert code_vector_store.main() == 1
