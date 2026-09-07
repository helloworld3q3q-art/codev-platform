"""CodeGraph 唯一受控恢复状态机测试。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest


_TARGET = "a" * 40
_RUNTIME_REVISION = "b" * 40


def _context(tmp_path: Path):
    from codev_platform.core.runtime_interpreter import ReleaseInterpreterIdentity
    from codev_platform.ops.reindex_codegraph_resume_context import CodegraphResumeContext

    repository = tmp_path / "repo"
    repository.mkdir()
    return CodegraphResumeContext(
        project_id="demo",
        target_commit=_TARGET,
        runtime_release=ReleaseInterpreterIdentity(
            runtime_revision=_RUNTIME_REVISION,
            release_id="c" * 64,
            interpreter_path="/release/venv/bin/python",
        ),
        config_path=(tmp_path / "overlay.json").resolve(),
        config_digest="b" * 64,
        data_root=(tmp_path / "data").resolve(),
        manifest_path=(tmp_path / "data" / "index_manifest.sqlite").resolve(),
        repositories=(repository.resolve(),),
        health_url="http://127.0.0.1:18091/healthz",
    )


def _ports(
    events: list[str],
    context,
    *,
    fail_at: str | None = None,
    fail_on_occurrence: int = 1,
    fail_prepare_on: int | None = None,
    fail_lease_enter: bool = False,
    fail_lease_exit: bool = False,
    lease_exit_error: BaseException | None = None,
    fail_transition_exit: bool = False,
    transition_enter_error: BaseException | None = None,
    transition_exit_error: BaseException | None = None,
):
    from codev_platform.ops.reindex_codegraph_resume import CodegraphResumePorts
    from codev_platform.ops.reindex_codegraph_runtime_identity import (
        CodegraphRuntimeIdentity,
    )
    from codev_platform.ops.reindex_restore_handoff import ReindexRestoreHandoff

    occurrences: dict[str, int] = {}

    def stage(name: str) -> None:
        events.append(name)
        occurrences[name] = occurrences.get(name, 0) + 1
        if name == fail_at and occurrences[name] == fail_on_occurrence:
            raise RuntimeError(name)

    @contextmanager
    def leases(repositories):
        assert tuple(repositories) == context.repositories
        events.append("lease-enter")
        if fail_lease_enter:
            raise RuntimeError("lease-enter")
        try:
            yield
        finally:
            events.append("lease-exit")
            if fail_lease_exit:
                raise RuntimeError("lease-exit")
            if lease_exit_error is not None:
                raise lease_exit_error

    def prepare() -> None:
        stage("prepare")
        if occurrences["prepare"] == fail_prepare_on:
            raise RuntimeError("prepare")

    @contextmanager
    def session():
        yield

    @contextmanager
    def transition():
        events.append("transition-enter")
        if transition_enter_error is not None:
            raise transition_enter_error
        try:
            yield
        finally:
            events.append("transition-exit")
            if transition_exit_error is not None:
                raise transition_exit_error
            if fail_transition_exit:
                raise RuntimeError("transition-exit")

    return CodegraphResumePorts(
        resolve_context=lambda _project, _target: stage("context") or context,
        verify_configuration=lambda _context: stage("config"),
        inspect_maintenance=lambda: stage("inspect"),
        operation_leases=leases,
        prove_manifest=lambda _context: stage("manifest"),
        prepare_reindex_handoff=lambda _context, _identity: (
            stage("reindex-prepare") or ReindexRestoreHandoff("c" * 32, "d" * 32)
        ),
        complete_reindex_handoff_locked=lambda _handoff: stage("reindex-commit"),
        prove_codegraph_maintenance=lambda: stage("codegraph-maintenance"),
        clear_codegraph_startup_bridge=lambda _context: stage("bridge-clear"),
        prove_staged_payload=lambda _context: stage("staged"),
        install_codegraph_startup_bridge=lambda _context: stage("bridge-install"),
        remove_runtime_mask=lambda: stage("unmask"),
        prove_codegraph_effective_payload=lambda _context: stage("effective"),
        enable_codegraph=lambda: stage("enable"),
        remove_codegraph_hold=lambda: stage("hold-off"),
        start_codegraph=lambda: stage("start"),
        prove_codegraph_running=lambda _context: stage("running"),
        read_codegraph_identity=lambda _context: (
            stage("codegraph-identity") or CodegraphRuntimeIdentity("e" * 32, 0)
        ),
        prove_codegraph_health=lambda _context: stage("health"),
        prove_codegraph_stable=lambda _context, _identity: stage("codegraph-stable"),
        remove_codegraph_startup_bridge=lambda _context: stage("bridge-remove"),
        prove_codegraph_normal_effective_payload=lambda _context: stage("normal-effective"),
        settle_maintenance_locked=lambda _context: stage("settle-maintenance") or True,
        prepare_maintenance=prepare,
        transition_session_lock=session,
        transition_lock=transition,
    )


def test_受控恢复在多仓租约内按唯一顺序恢复服务(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume import resume_codegraph

    events: list[str] = []
    context = _context(tmp_path)

    resume_codegraph(
        project_id="demo",
        target_commit=_TARGET,
        ports=_ports(events, context),
    )

    assert events == [
        "context",
        "config",
        "prepare",
        "bridge-clear",
        "inspect",
        "lease-enter",
        "inspect",
        "manifest",
        "config",
        "transition-enter",
        "codegraph-maintenance",
        "config",
        "staged",
        "bridge-install",
        "unmask",
        "effective",
        "hold-off",
        "start",
        "running",
        "codegraph-identity",
        "health",
        "codegraph-stable",
        "bridge-remove",
        "normal-effective",
        "running",
        "codegraph-identity",
        "health",
        "codegraph-stable",
        "enable",
        "transition-exit",
        "reindex-prepare",
        "transition-enter",
        "reindex-commit",
        "transition-exit",
        "lease-exit",
    ]


def test_长EX内reindex保持停止并在锁外开始最终交接(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume import resume_codegraph

    events: list[str] = []
    context = _context(tmp_path)
    state = {
        "exclusive": False,
        "marker": True,
        "phase": 0,
        "session": False,
        "worker_stopped": True,
    }

    @contextmanager
    def session():
        state["session"] = True
        try:
            yield
        finally:
            state["session"] = False

    @contextmanager
    def transition():
        assert state["session"] is True
        state["phase"] += 1
        state["exclusive"] = True
        events.append("transition-enter")
        try:
            yield
        finally:
            state["exclusive"] = False
            events.append("transition-exit")

    def prepare_reindex(_context, _identity):
        assert state == {
            "exclusive": False,
            "marker": True,
            "phase": 1,
            "session": True,
            "worker_stopped": True,
        }
        state["worker_stopped"] = False
        events.append("reindex-prepare")
        from codev_platform.ops.reindex_restore_handoff import ReindexRestoreHandoff

        return ReindexRestoreHandoff("c" * 32, "d" * 32)

    def complete_reindex(_handoff) -> None:
        assert state == {
            "exclusive": True,
            "marker": True,
            "phase": 2,
            "session": True,
            "worker_stopped": False,
        }
        state["marker"] = False
        events.append("reindex-commit")

    ports = replace(
        _ports(events, context),
        transition_lock=transition,
        transition_session_lock=session,
        prepare_reindex_handoff=prepare_reindex,
        complete_reindex_handoff_locked=complete_reindex,
    )

    resume_codegraph(project_id="demo", target_commit=_TARGET, ports=ports)

    first_transition_exit = events.index("transition-exit")
    assert first_transition_exit < events.index("reindex-prepare")
    assert events.index("reindex-prepare") < events.index("reindex-commit")


def test_锁外reindex恢复失败时重新收敛完整maintenance(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume import (
        CodegraphResumeError,
        resume_codegraph,
    )

    events: list[str] = []
    context = _context(tmp_path)

    with pytest.raises(CodegraphResumeError, match="已回到维护状态"):
        resume_codegraph(
            project_id="demo",
            target_commit=_TARGET,
            ports=_ports(events, context, fail_at="reindex-prepare"),
        )

    assert events.index("transition-exit") < events.index("reindex-prepare")
    assert events.index("reindex-prepare") < len(events) - 3
    assert events[-4:] == ["prepare", "bridge-clear", "inspect", "lease-exit"]


def test_长EX期间worker保持停止且锁外交接后才进入写段(
    tmp_path: Path,
) -> None:
    """冻结 worker 不得跨越长 EX；最终交接完成后仍受仓租约约束。"""
    from codev_platform.ops.reindex_codegraph_resume import resume_codegraph

    events: list[str] = []
    context = _context(tmp_path)
    state = {
        "exclusive": False,
        "marker": True,
        "phase": 0,
        "repo_lease": False,
        "worker_stopped": True,
    }

    def worker_attempt() -> None:
        if state["worker_stopped"]:
            events.append("worker-stopped")
        elif state["marker"]:
            events.append("worker-marker-blocked")
        elif state["exclusive"]:
            events.append("worker-ex-blocked")
        elif state["repo_lease"]:
            events.append("worker-repo-blocked")
        else:
            events.append("worker-write")

    @contextmanager
    def leases(_repositories):
        state["repo_lease"] = True
        events.append("lease-enter")
        try:
            yield
        finally:
            state["repo_lease"] = False
            events.append("lease-exit")
            worker_attempt()

    @contextmanager
    def transition():
        state["phase"] += 1
        state["exclusive"] = True
        events.append("transition-enter")
        worker_attempt()
        try:
            yield
        finally:
            state["exclusive"] = False
            events.append("transition-exit")
            worker_attempt()

    def prepare_reindex(_context, _identity):
        assert state["exclusive"] is False
        assert state["worker_stopped"] is True
        assert state["phase"] == 1
        events.append("reindex-prepare")
        state["worker_stopped"] = False
        from codev_platform.ops.reindex_restore_handoff import ReindexRestoreHandoff

        return ReindexRestoreHandoff("c" * 32, "d" * 32)

    def complete_reindex(_handoff) -> None:
        assert state["exclusive"] is True
        assert state["marker"] is True
        assert state["phase"] == 2
        events.append("reindex-commit")
        state["marker"] = False
        worker_attempt()

    ports = replace(
        _ports(events, context),
        operation_leases=lambda repositories: leases(repositories),
        transition_lock=transition,
        prepare_reindex_handoff=prepare_reindex,
        complete_reindex_handoff_locked=complete_reindex,
    )

    resume_codegraph(project_id="demo", target_commit=_TARGET, ports=ports)

    first_transition_exit = events.index("transition-exit")
    second_transition_exit = events.index("transition-exit", first_transition_exit + 1)
    assert events.index("worker-stopped") < first_transition_exit
    assert first_transition_exit < events.index("reindex-prepare")
    assert events.index("reindex-prepare") < events.index("reindex-commit")
    assert events.index("reindex-commit") < events.index("worker-ex-blocked")
    assert events.index("worker-ex-blocked") < second_transition_exit
    assert events.index("worker-repo-blocked") < events.index("lease-exit")
    assert events[-1] == "worker-write"


def test_任一持久化前缀掉电都保持marker或已完成全部提交前证明(
    tmp_path: Path,
) -> None:
    """不运行异常补偿来模拟掉电；marker 删除只能出现在全部健康证明之后。"""
    from codev_platform.ops.reindex_codegraph_resume import resume_codegraph
    from codev_platform.ops.reindex_codegraph_runtime_identity import (
        CodegraphRuntimeIdentity,
    )
    from codev_platform.ops.reindex_restore_handoff import ReindexRestoreHandoff

    events: list[str] = []
    context = _context(tmp_path)
    identity = CodegraphRuntimeIdentity("e" * 32, 0)
    state = {
        "marker": True,
        "healthy": False,
        "stable": False,
        "enabled": False,
        "reindex_prepared": False,
        "reindex_restored": False,
    }
    prefixes: list[dict[str, bool]] = []

    def checkpoint(name: str, **changes: bool) -> None:
        events.append(name)
        state.update(changes)
        prefixes.append(dict(state))

    ports = replace(
        _ports(events, context),
        remove_runtime_mask=lambda: checkpoint("unmask"),
        remove_codegraph_hold=lambda: checkpoint("hold-off"),
        start_codegraph=lambda: checkpoint("start"),
        read_codegraph_identity=lambda _context: checkpoint("codegraph-identity") or identity,
        prove_codegraph_health=lambda _context: checkpoint("health", healthy=True),
        prove_codegraph_stable=lambda _context, _identity: checkpoint(
            "codegraph-stable", stable=True
        ),
        enable_codegraph=lambda: checkpoint("enable", enabled=True),
        prepare_reindex_handoff=lambda _context, _identity: (
            checkpoint("reindex-prepare", reindex_prepared=True)
            or ReindexRestoreHandoff("c" * 32, "d" * 32)
        ),
        complete_reindex_handoff_locked=lambda _handoff: checkpoint(
            "reindex-commit", marker=False, reindex_restored=True
        ),
    )

    resume_codegraph(project_id="demo", target_commit=_TARGET, ports=ports)

    for prefix in prefixes:
        assert prefix["marker"] or (
            prefix["healthy"]
            and prefix["stable"]
            and prefix["enabled"]
            and prefix["reindex_prepared"]
            and prefix["reindex_restored"]
        )
    assert events[-4:] == [
        "transition-enter",
        "reindex-commit",
        "transition-exit",
        "lease-exit",
    ]


def test_manifest失败时不恢复reindex或解除CodeGraph_mask(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume import (
        CodegraphResumeError,
        resume_codegraph,
    )

    events: list[str] = []
    with pytest.raises(CodegraphResumeError, match="前置验证失败"):
        resume_codegraph(
            project_id="demo",
            target_commit=_TARGET,
            ports=_ports(events, _context(tmp_path), fail_at="manifest"),
        )

    assert events == [
        "context",
        "config",
        "prepare",
        "bridge-clear",
        "inspect",
        "lease-enter",
        "inspect",
        "manifest",
        "prepare",
        "bridge-clear",
        "inspect",
        "lease-exit",
    ]


@pytest.mark.parametrize(
    ("failure_stage", "failure_occurrence"),
    [
        ("config", 2),
        ("codegraph-maintenance", 1),
        ("staged", 1),
        ("bridge-install", 1),
        ("unmask", 1),
        ("effective", 1),
        ("enable", 1),
        ("hold-off", 1),
        ("start", 1),
        ("running", 1),
        ("codegraph-identity", 1),
        ("health", 1),
        ("codegraph-stable", 1),
        ("bridge-remove", 1),
        ("normal-effective", 1),
        ("reindex-prepare", 1),
        ("reindex-commit", 1),
    ],
)
def test_组合交接任一失败均在租约内回到维护状态(
    tmp_path: Path,
    failure_stage: str,
    failure_occurrence: int,
) -> None:
    from codev_platform.ops.reindex_codegraph_resume import (
        CodegraphResumeError,
        resume_codegraph,
    )

    events: list[str] = []
    with pytest.raises(CodegraphResumeError, match="已回到维护状态"):
        resume_codegraph(
            project_id="demo",
            target_commit=_TARGET,
            ports=_ports(
                events,
                _context(tmp_path),
                fail_at=failure_stage,
                fail_on_occurrence=failure_occurrence,
            ),
        )

    outside_transition = failure_stage in {"config", "reindex-prepare"}
    expected = (
        ["prepare", "bridge-clear", "inspect", "lease-exit"]
        if outside_transition
        else ["settle-maintenance", "transition-exit", "lease-exit"]
    )
    assert events[-len(expected) :] == expected
    assert events.count("prepare") == (2 if outside_transition else 1)


def test_锁内补偿证明失败时不得宣称已回到维护状态(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume import (
        CodegraphResumeError,
        resume_codegraph,
    )

    events: list[str] = []
    ports = replace(
        _ports(events, _context(tmp_path), fail_at="unmask"),
        settle_maintenance_locked=lambda _context: events.append("settle-maintenance") or False,
    )
    with pytest.raises(CodegraphResumeError, match="安全状态未证明"):
        resume_codegraph(
            project_id="demo",
            target_commit=_TARGET,
            ports=ports,
        )

    assert events[-3:] == ["settle-maintenance", "transition-exit", "lease-exit"]
    assert events.count("prepare") == 1


def test_租约申请失败时沿用进入前已证明维护状态且不重入转换锁(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume import (
        CodegraphResumeError,
        resume_codegraph,
    )

    events: list[str] = []
    with pytest.raises(CodegraphResumeError, match="已回到维护状态"):
        resume_codegraph(
            project_id="demo",
            target_commit=_TARGET,
            ports=_ports(events, _context(tmp_path), fail_lease_enter=True),
        )

    assert events[-4:] == ["prepare", "bridge-clear", "inspect", "lease-enter"]
    assert events.count("prepare") == 1


def test_租约释放失败时未知ownership禁止重入维护转换锁(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume import (
        CodegraphResumeError,
        resume_codegraph,
    )

    events: list[str] = []
    with pytest.raises(CodegraphResumeError, match="安全状态未证明"):
        resume_codegraph(
            project_id="demo",
            target_commit=_TARGET,
            ports=_ports(events, _context(tmp_path), fail_lease_exit=True),
        )

    assert events[-1:] == ["lease-exit"]
    assert events.count("prepare") == 1


def test_租约释放恢复异常时仍禁止重入维护转换锁(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume import (
        CodegraphResumeError,
        resume_codegraph,
    )

    events: list[str] = []
    with pytest.raises(CodegraphResumeError, match="安全状态未证明"):
        resume_codegraph(
            project_id="demo",
            target_commit=_TARGET,
            ports=_ports(
                events,
                _context(tmp_path),
                lease_exit_error=CodegraphResumeError("租约退出异常"),
            ),
        )

    assert events[-1:] == ["lease-exit"]
    assert events.count("prepare") == 1


def test_systemd转换锁释放失败时不得在未知锁状态下重入维护补偿(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume import (
        CodegraphResumeError,
        resume_codegraph,
    )

    events: list[str] = []
    with pytest.raises(CodegraphResumeError, match="安全状态未证明"):
        resume_codegraph(
            project_id="demo",
            target_commit=_TARGET,
            ports=_ports(events, _context(tmp_path), fail_transition_exit=True),
        )

    assert events[-2:] == ["transition-exit", "lease-exit"]
    assert events.count("prepare") == 1


def test_转换锁异常再叠加租约退出异常时仍不得重入维护补偿(tmp_path: Path) -> None:
    """租约退出覆盖原异常时，原转换锁未知状态仍优先于补偿。"""
    from codev_platform.ops.reindex_codegraph_resume import (
        CodegraphResumeError,
        resume_codegraph,
    )

    events: list[str] = []
    with pytest.raises(CodegraphResumeError, match="安全状态未证明"):
        resume_codegraph(
            project_id="demo",
            target_commit=_TARGET,
            ports=_ports(
                events,
                _context(tmp_path),
                fail_transition_exit=True,
                fail_lease_exit=True,
            ),
        )

    assert events.count("prepare") == 1


def test_转换锁中断被租约退出异常覆盖时仍原样上抛(tmp_path: Path) -> None:
    """进程级中断优先，且异常链上的未知锁状态绝不能触发补偿重入。"""
    from codev_platform.ops.reindex_codegraph_resume import resume_codegraph

    events: list[str] = []
    with pytest.raises(KeyboardInterrupt):
        resume_codegraph(
            project_id="demo",
            target_commit=_TARGET,
            ports=_ports(
                events,
                _context(tmp_path),
                transition_exit_error=KeyboardInterrupt(),
                fail_lease_exit=True,
            ),
        )

    assert events.count("prepare") == 1


@pytest.mark.parametrize("phase", ["enter", "exit"])
def test_systemd转换锁中断时不得在未知锁状态下重入维护补偿(
    tmp_path: Path,
    phase: str,
) -> None:
    """转换锁本身的进程级中断必须原样上抛，且绝不能再次申请该锁。"""
    from codev_platform.ops.reindex_codegraph_resume import resume_codegraph

    events: list[str] = []
    keyword = (
        {"transition_enter_error": KeyboardInterrupt()}
        if phase == "enter"
        else {"transition_exit_error": KeyboardInterrupt()}
    )

    with pytest.raises(KeyboardInterrupt):
        resume_codegraph(
            project_id="demo",
            target_commit=_TARGET,
            ports=_ports(events, _context(tmp_path), **keyword),
        )

    assert events.count("prepare") == 1


@pytest.mark.parametrize("interrupted", [False, True])
def test_systemd转换锁工厂异常时不得在未知锁状态下重入维护补偿(
    tmp_path: Path,
    interrupted: bool,
) -> None:
    """锁对象尚未创建也属于未知 ownership，不能为补偿再次申请转换锁。"""
    from codev_platform.ops.reindex_codegraph_resume import (
        CodegraphResumeError,
        resume_codegraph,
    )

    events: list[str] = []
    failure: BaseException = KeyboardInterrupt() if interrupted else RuntimeError("lock factory")

    def 失败工厂():
        raise failure

    ports = replace(_ports(events, _context(tmp_path)), transition_lock=失败工厂)
    expected = KeyboardInterrupt if interrupted else CodegraphResumeError
    with pytest.raises(expected, match=None if interrupted else "安全状态未证明"):
        resume_codegraph(project_id="demo", target_commit=_TARGET, ports=ports)

    assert events.count("prepare") == 1


def test_stage_receipt与目标不一致时不得解除CodeGraph_mask(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume import (
        CodegraphResumeError,
        resume_codegraph,
    )

    events: list[str] = []
    ports = replace(
        _ports(events, _context(tmp_path)),
        prove_staged_payload=lambda _context: (_ for _ in ()).throw(
            RuntimeError("stage receipt 不匹配")
        ),
    )

    with pytest.raises(CodegraphResumeError, match="已回到维护状态"):
        resume_codegraph(project_id="demo", target_commit=_TARGET, ports=ports)

    assert "unmask" not in events
    assert "start" not in events
    assert "health" not in events


def test_CodeGraph解除mask后有效载荷证明失败时禁止启动(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume import (
        CodegraphResumeError,
        resume_codegraph,
    )

    events: list[str] = []
    with pytest.raises(CodegraphResumeError, match="已回到维护状态"):
        resume_codegraph(
            project_id="demo",
            target_commit=_TARGET,
            ports=_ports(events, _context(tmp_path), fail_at="effective"),
        )

    assert events.index("unmask") < events.index("effective")
    assert "start" not in events
    assert events[-3:] == ["settle-maintenance", "transition-exit", "lease-exit"]


def test_租约吞掉体内异常时仍以受控失败结束(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume import (
        CodegraphResumeError,
        resume_codegraph,
    )

    events: list[str] = []

    class SuppressingLeases:
        def __enter__(self) -> None:
            events.append("lease-enter")
            return None

        def __exit__(self, *_args: object) -> bool:
            events.append("lease-exit")
            return True

    ports = replace(
        _ports(events, _context(tmp_path), fail_at="manifest"),
        operation_leases=lambda _repositories: SuppressingLeases(),
    )
    with pytest.raises(CodegraphResumeError, match="前置验证失败；已回到维护状态"):
        resume_codegraph(project_id="demo", target_commit=_TARGET, ports=ports)

    assert events[-5:] == [
        "manifest",
        "prepare",
        "bridge-clear",
        "inspect",
        "lease-exit",
    ]


def test_锁外恢复再次中断时仍在finally释放已取得租约(tmp_path: Path) -> None:
    """恢复动作的二次中断不能把多仓操作租约永久遗留到进程退出。"""
    from codev_platform.ops.reindex_codegraph_resume import resume_codegraph

    events: list[str] = []
    ports = _ports(events, _context(tmp_path), fail_at="manifest")
    prepare_calls = 0

    def prepare() -> None:
        nonlocal prepare_calls
        prepare_calls += 1
        events.append("prepare")
        if prepare_calls == 2:
            raise KeyboardInterrupt("恢复期中断")

    with pytest.raises(KeyboardInterrupt, match="恢复期中断"):
        resume_codegraph(
            project_id="demo",
            target_commit=_TARGET,
            ports=replace(ports, prepare_maintenance=prepare),
        )

    assert events[-2:] == ["prepare", "lease-exit"]


def test_体内已恢复后租约释放失败不再次重入维护转换锁(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume import (
        CodegraphResumeError,
        resume_codegraph,
    )

    events: list[str] = []
    with pytest.raises(CodegraphResumeError, match="已回到维护状态"):
        resume_codegraph(
            project_id="demo",
            target_commit=_TARGET,
            ports=_ports(
                events,
                _context(tmp_path),
                fail_at="manifest",
                fail_lease_exit=True,
            ),
        )

    assert events[-5:] == [
        "manifest",
        "prepare",
        "bridge-clear",
        "inspect",
        "lease-exit",
    ]
    assert events.count("prepare") == 2


def test_配置同源失败时不进入维护状态机或申请租约(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume import (
        CodegraphResumeError,
        resume_codegraph,
    )

    events: list[str] = []
    with pytest.raises(CodegraphResumeError, match="前置验证失败"):
        resume_codegraph(
            project_id="demo",
            target_commit=_TARGET,
            ports=_ports(events, _context(tmp_path), fail_at="config"),
        )

    assert events == ["context", "config"]


def test_默认载荷证明绑定完整发布身份而非项目目标提交(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_stage_receipt as receipt
    from codev_platform.ops import reindex_codegraph_resume_adapters as adapters
    from codev_platform.ops import reindex_codegraph_resume as resume
    from codev_platform.ops import reindex_maintenance

    context = _context(tmp_path)
    bridge = SimpleNamespace(
        dropin_content=b"[Service]\nExecStart=bridge\n",
        exec_start=("/release/venv/bin/python", "-I", "-B"),
    )
    calls: list[
        tuple[object, bool, bytes | None, bool, int | None, bytes | None, tuple[str, ...] | None]
    ] = []
    monkeypatch.setattr(
        receipt,
        "prove_staged_systemd_payload",
        lambda release, *, require_effective, resume_dropin_content=None, allow_reindex_local_dropins=False, codegraph_port=None, codegraph_startup_bridge_dropin_content=None, codegraph_effective_exec_start=None: (
            calls.append(
                (
                    release,
                    require_effective,
                    resume_dropin_content,
                    allow_reindex_local_dropins,
                    codegraph_port,
                    codegraph_startup_bridge_dropin_content,
                    codegraph_effective_exec_start,
                )
            )
        ),
    )
    monkeypatch.setattr(
        reindex_maintenance,
        "prove_reindex_maintenance_dropin",
        lambda: None,
    )
    monkeypatch.setattr(
        adapters,
        "_codegraph_startup_bridge_spec",
        lambda _context: bridge,
    )

    resume._default_staged_payload_proof(context)
    resume._default_codegraph_effective_payload_proof(context)
    resume._default_codegraph_normal_effective_payload_proof(context)

    assert calls == [
        (context.runtime_release, False, None, False, 18091, None, None),
        (
            context.runtime_release,
            True,
            (
                b"[Service]\nEnvironmentFile=\n"
                b"EnvironmentFile=/etc/codev-platform/reindex-codegraph-resume.env\n"
            ),
            True,
            18091,
            bridge.dropin_content,
            bridge.exec_start,
        ),
        (
            context.runtime_release,
            True,
            (
                b"[Service]\nEnvironmentFile=\n"
                b"EnvironmentFile=/etc/codev-platform/reindex-codegraph-resume.env\n"
            ),
            True,
            18091,
            None,
            None,
        ),
    ]


def test_默认端口把同一上下文接到配置manifest租约与healthz(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform.codegraph import operation_lease
    from codev_platform.ops import reindex_admin_systemd_guard as guard
    from codev_platform.ops import reindex_codegraph_lifecycle as lifecycle
    from codev_platform.ops import reindex_codegraph_maintenance as maintenance
    from codev_platform.ops import reindex_codegraph_manifest_proof as manifest
    from codev_platform.ops import reindex_codegraph_resume as resume
    from codev_platform.ops import reindex_codegraph_resume_config_proof as config_proof
    from codev_platform.ops import reindex_codegraph_resume_context as context_module
    from codev_platform.ops import reindex_codegraph_resume_health as health
    from codev_platform.ops import reindex_codegraph_runtime_identity as runtime_identity
    from codev_platform.ops import reindex_maintenance as reindex_maintenance
    from codev_platform.ops.reindex_restore_handoff import ReindexRestoreHandoff

    context = _context(tmp_path)
    events: list[object] = []

    @contextmanager
    def leases(repositories):
        assert tuple(repositories) == context.repositories
        events.append("lease-enter")
        try:
            yield
        finally:
            events.append("lease-exit")

    monkeypatch.setattr(context_module, "resolve_codegraph_resume_context", lambda *_args: context)
    monkeypatch.setattr(
        config_proof,
        "verify_codegraph_resume_configuration",
        lambda **kwargs: events.append(("config", kwargs)),
    )
    monkeypatch.setattr(
        reindex_maintenance, "prepare_reindex_maintenance", lambda: events.append("prepare")
    )
    monkeypatch.setattr(
        reindex_maintenance, "inspect_reindex_maintenance", lambda: events.append("inspect")
    )
    monkeypatch.setattr(operation_lease, "codegraph_operation_leases", leases)
    monkeypatch.setattr(
        manifest,
        "require_codegraph_manifest_target_ok",
        lambda *args, **kwargs: events.append(("manifest", args, kwargs)),
    )
    handoff = ReindexRestoreHandoff("c" * 32, "d" * 32)
    monkeypatch.setattr(
        resume,
        "_default_prepare_reindex_handoff",
        lambda current, identity: events.append(("reindex-prepare", current, identity)) or handoff,
    )
    monkeypatch.setattr(
        resume,
        "_default_complete_reindex_handoff_locked",
        lambda token: events.append(("reindex-commit", token)),
    )
    monkeypatch.setattr(
        lifecycle, "verify_codegraph_maintenance", lambda: events.append("codegraph-maintenance")
    )
    monkeypatch.setattr(
        resume,
        "_default_staged_payload_proof",
        lambda current: events.append(("staged", current.runtime_revision)),
    )
    monkeypatch.setattr(
        resume,
        "_default_clear_codegraph_startup_bridge",
        lambda current: events.append(("bridge-clear", current.runtime_revision)),
    )
    monkeypatch.setattr(
        resume,
        "_default_install_codegraph_startup_bridge",
        lambda current: events.append(("bridge-install", current.runtime_revision)),
    )
    monkeypatch.setattr(
        maintenance, "remove_codegraph_runtime_mask", lambda: events.append("unmask")
    )
    monkeypatch.setattr(
        resume,
        "_default_codegraph_effective_payload_proof",
        lambda current: events.append(("effective", current.runtime_revision)),
    )
    monkeypatch.setattr(
        resume,
        "_default_remove_codegraph_startup_bridge",
        lambda current: events.append(("bridge-remove", current.runtime_revision)),
    )
    monkeypatch.setattr(
        resume,
        "_default_codegraph_normal_effective_payload_proof",
        lambda current: events.append(("normal-effective", current.runtime_revision)),
    )
    monkeypatch.setattr(resume, "_default_remove_codegraph_hold", lambda: events.append("hold-off"))
    monkeypatch.setattr(lifecycle, "start_codegraph_service", lambda: events.append("start"))
    monkeypatch.setattr(guard, "verify_codev_codegraph_running", lambda: events.append("running"))
    identity = runtime_identity.CodegraphRuntimeIdentity("e" * 32, 0)
    monkeypatch.setattr(
        runtime_identity,
        "read_codegraph_runtime_identity",
        lambda: events.append("codegraph-identity") or identity,
    )
    monkeypatch.setattr(
        health, "prove_codegraph_health", lambda url: events.append(("health", url))
    )
    monkeypatch.setattr(
        runtime_identity,
        "prove_codegraph_runtime_stable",
        lambda current: events.append(("codegraph-stable", current)),
    )
    monkeypatch.setattr(lifecycle, "enable_codegraph_service", lambda: events.append("enable"))
    resume.resume_codegraph(project_id="demo", target_commit=_TARGET)

    assert events[0] == (
        "config",
        {
            "config_path": context.config_path,
            "data_root": context.data_root,
            "config_digest": context.config_digest,
        },
    )
    assert (
        "manifest",
        ("demo", _TARGET, _RUNTIME_REVISION),
        {"path": context.manifest_path},
    ) in events
    assert ("staged", _RUNTIME_REVISION) in events
    assert ("bridge-clear", _RUNTIME_REVISION) in events
    assert ("bridge-install", _RUNTIME_REVISION) in events
    assert ("effective", _RUNTIME_REVISION) in events
    assert ("bridge-remove", _RUNTIME_REVISION) in events
    assert ("normal-effective", _RUNTIME_REVISION) in events
    assert events.index("start") < events.index(("reindex-prepare", context, identity))
    assert events[-5:] == [
        ("codegraph-stable", identity),
        "enable",
        ("reindex-prepare", context, identity),
        ("reindex-commit", handoff),
        "lease-exit",
    ]
