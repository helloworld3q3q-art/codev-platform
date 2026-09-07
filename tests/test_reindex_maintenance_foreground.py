"""维护窗口对前台 reindex 入口的门禁回归测试。"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from codev_platform.ops.reindex import commands, dispatch


@contextmanager
def _维护许可(permitted: bool) -> Iterator[bool]:
    """用最小上下文替身表达维护许可，避免测试复制生产判定。"""
    yield permitted


@contextmanager
def _可观测维护许可(permitted: bool, state: dict[str, bool]) -> Iterator[bool]:
    """验证前台写操作在整个执行期内持续持有统一维护许可。"""
    state["held"] = True
    try:
        yield permitted
    finally:
        state["held"] = False


def _reindex_args(**overrides: object) -> argparse.Namespace:
    """构造完整 reindex 参数，显式避免依赖 parser 默认值。"""
    values: dict[str, object] = {
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
    values.update(overrides)
    return argparse.Namespace(**values)


def _configure_foreground_dispatch(monkeypatch, enqueued: list[tuple[str, str]]) -> None:
    """为共享 hook dispatch 提供不触碰真实队列的最小入队环境。"""

    class _Queue:
        def enqueue(self, project_id: str, kind: str, *, meta) -> None:
            enqueued.append((project_id, kind))

    monkeypatch.setattr(dispatch.C, "config", lambda: {})
    monkeypatch.setattr(dispatch.C, "project_id_of", lambda _repo: "demo-proj")
    monkeypatch.setattr(dispatch.C, "meta_health", lambda _project_id: {})
    monkeypatch.setattr(
        dispatch,
        "impacted_project_ids_for_repo",
        lambda _repo, **_kwargs: ["demo-proj"],
    )
    monkeypatch.setattr(
        "codev_platform.reindex.open_default_queue",
        lambda **_kwargs: _Queue(),
    )
    monkeypatch.setattr(
        "codev_platform.reindex.target_commit.resolve_repo_head",
        lambda _repo: "e" * 40,
    )


def test_维护窗口拒绝foreground_dispatch且不初始化worker运行资源(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """共享 hook 仍可记录待办，但维护期间绝不能进入前台 drain。"""
    enqueued: list[tuple[str, str]] = []
    _configure_foreground_dispatch(monkeypatch, enqueued)
    monkeypatch.setattr(
        dispatch,
        "maintenance_reindex_operation_permit",
        lambda: _维护许可(False),
        raising=False,
    )
    monkeypatch.setattr(
        "codev_platform.core.config.load_config",
        lambda: pytest.fail("维护窗口拒绝后不得读取前台 worker 配置"),
    )
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.acquire_run_lock",
        lambda *_args, **_kwargs: pytest.fail("维护窗口拒绝后不得获取运行锁"),
    )

    assert (
        dispatch._dispatch_reindex(
            tmp_path,
            ["apps/web/src/Foo.java"],
            foreground=True,
            trigger_line="post-commit: deadbeef",
            banner="post-commit",
        )
        == 0
    )

    assert enqueued
    assert "维护窗口" in capsys.readouterr().err


def test_受控维护许可允许foreground_dispatch完成隔离drain(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """systemd 受控 cgroup 的恢复进程可在 marker 存在时完成 drain。"""
    enqueued: list[tuple[str, str]] = []
    calls: list[str] = []
    permit_state = {"held": False}
    _configure_foreground_dispatch(monkeypatch, enqueued)
    monkeypatch.setattr(
        dispatch,
        "maintenance_reindex_operation_permit",
        lambda: _可观测维护许可(True, permit_state),
        raising=False,
    )

    class _Loop:
        def drain_once(self) -> int:
            assert permit_state["held"] is True
            calls.append("drain")
            return 1

    class _Runtime:
        loop = _Loop()

    def _build_runtime(_cfg, _owner):
        assert permit_state["held"] is True
        calls.append("build")
        return _Runtime()

    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: {})
    monkeypatch.setattr(
        "codev_platform.reindex.isolated_worker_composer.build_isolated_worker",
        _build_runtime,
    )

    assert (
        dispatch._dispatch_reindex(
            tmp_path,
            ["apps/web/src/Foo.java"],
            foreground=True,
            trigger_line="post-merge: deadbeef",
            banner="post-merge",
        )
        == 0
    )

    assert enqueued
    assert calls == ["build", "drain"]
    assert permit_state["held"] is False


def test_维护窗口拒绝cmd_reindex且不进入任何写阶段(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """直接完整或分段 reindex 也必须在所有 stage 之前被维护门禁拒绝。"""
    monkeypatch.setattr(commands.C, "resolve_repo", lambda _repo: tmp_path)
    monkeypatch.setattr(
        commands,
        "maintenance_reindex_operation_permit",
        lambda: _维护许可(False),
        raising=False,
    )
    monkeypatch.setattr(
        commands,
        "_bound_reindex_project",
        lambda *_args: pytest.fail("维护窗口拒绝后不得解析运行时身份"),
    )
    monkeypatch.setattr(
        commands,
        "_run_codegraph_stage",
        lambda *_args: pytest.fail("维护窗口拒绝后不得写 codegraph"),
    )

    assert commands.cmd_reindex(_reindex_args(codegraph=True)) == 1
    assert "维护窗口" in capsys.readouterr().err


def test_受控维护许可允许cmd_reindex进入目标stage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """受控 cgroup 的服务恢复路径不应被 marker 错误阻断。"""
    stages: list[str] = []
    permit_state = {"held": False}
    monkeypatch.setattr(commands.C, "resolve_repo", lambda _repo: tmp_path)
    monkeypatch.setattr(
        commands,
        "maintenance_reindex_operation_permit",
        lambda: _可观测维护许可(True, permit_state),
        raising=False,
    )
    monkeypatch.setattr(commands, "_bound_reindex_project", lambda *_args: "demo-proj")

    def _run_codegraph(*_args):
        assert permit_state["held"] is True
        stages.append("codegraph")
        return False, 0

    def _run_code_vector(*_args, **_kwargs):
        assert permit_state["held"] is True
        stages.append("code_vec")
        return 0

    monkeypatch.setattr(
        commands,
        "_run_codegraph_stage",
        _run_codegraph,
    )
    monkeypatch.setattr(
        commands,
        "_run_code_vector_stage",
        _run_code_vector,
    )

    assert commands.cmd_reindex(_reindex_args(codegraph=True)) == 0
    assert stages == ["codegraph", "code_vec"]
    assert permit_state["held"] is False
