"""CodeGraph 分相恢复生产适配器的顺序与失败边界回归。"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest


def _context(tmp_path: Path):
    from codev_platform.core.runtime_interpreter import ReleaseInterpreterIdentity
    from codev_platform.ops.reindex_codegraph_resume_context import CodegraphResumeContext

    repository = tmp_path / "repo"
    repository.mkdir()
    return CodegraphResumeContext(
        project_id="demo",
        target_commit="a" * 40,
        runtime_release=ReleaseInterpreterIdentity(
            runtime_revision="b" * 40,
            release_id="c" * 64,
            interpreter_path="/release/venv/bin/python",
        ),
        config_path=(tmp_path / "overlay.json").resolve(),
        config_digest="d" * 64,
        data_root=(tmp_path / "data").resolve(),
        manifest_path=(tmp_path / "data" / "index_manifest.sqlite").resolve(),
        repositories=(repository.resolve(),),
        health_url="http://127.0.0.1:18091/healthz",
    )


def test_分相恢复在marker删除前完成入口验收且短锁只提交handoff(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform.ops import reindex_codegraph_resume_adapters as adapters
    from codev_platform.ops import reindex_restore_handoff as handoff_module
    from codev_platform.ops.reindex_codegraph_runtime_identity import (
        CodegraphRuntimeIdentity,
    )
    from codev_platform.ops.reindex_restore_handoff import ReindexRestoreHandoff
    from codev_platform.reindex import maintenance_gate

    context = _context(tmp_path)
    identity = CodegraphRuntimeIdentity("e" * 32, 0)
    handoff = ReindexRestoreHandoff("f" * 32, "a" * 32)
    events: list[str] = []

    @contextmanager
    def transition():
        events.append("短锁进入")
        try:
            yield
        finally:
            events.append("短锁退出")

    monkeypatch.setattr(
        adapters,
        "_prove_codegraph_and_held_webhook",
        lambda _context, _identity: events.append("M1边界"),
    )
    monkeypatch.setattr(
        handoff_module,
        "prepare_reindex_restore_handoff",
        lambda: events.append("待命准备") or handoff,
    )
    monkeypatch.setattr(
        handoff_module,
        "settle_reindex_restore_handoff_while_session_locked",
        lambda token: events.append(f"会话结算:{token.generation}"),
    )
    monkeypatch.setattr(
        adapters, "default_resume_ingress", lambda _context: events.append("入口验收")
    )
    monkeypatch.setattr(
        adapters,
        "_prove_final_data_plane",
        lambda _context, _identity: events.append("M3边界"),
    )
    monkeypatch.setattr(
        handoff_module,
        "prove_reindex_restore_handoff_while_session_locked",
        lambda token: events.append(f"最终待命证明:{token.invocation_id}"),
    )
    monkeypatch.setattr(
        handoff_module,
        "complete_reindex_restore_handoff_while_transition_locked",
        lambda token: events.append(f"删除marker:{token.generation}"),
    )

    with maintenance_gate.maintenance_systemd_transition_session():
        prepared = adapters.default_prepare_reindex_handoff(context, identity)
        with transition():
            adapters.default_complete_reindex_handoff_locked(prepared)

    assert events == [
        "M1边界",
        "待命准备",
        f"会话结算:{'f' * 32}",
        "M1边界",
        "入口验收",
        "M3边界",
        f"最终待命证明:{'a' * 32}",
        "短锁进入",
        f"删除marker:{'f' * 32}",
        "短锁退出",
    ]


def test_入口验收失败时不得进入最终marker提交(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform.ops import reindex_codegraph_resume_adapters as adapters
    from codev_platform.ops import reindex_restore_handoff as handoff_module
    from codev_platform.ops.reindex_codegraph_runtime_identity import (
        CodegraphRuntimeIdentity,
    )
    from codev_platform.ops.reindex_restore_handoff import ReindexRestoreHandoff
    from codev_platform.reindex import maintenance_gate

    events: list[str] = []
    handoff = ReindexRestoreHandoff("f" * 32, "a" * 32)
    monkeypatch.setattr(
        adapters,
        "_prove_codegraph_and_held_webhook",
        lambda _context, _identity: events.append("M1边界"),
    )
    monkeypatch.setattr(
        handoff_module,
        "prepare_reindex_restore_handoff",
        lambda: events.append("待命准备") or handoff,
    )
    monkeypatch.setattr(
        handoff_module,
        "settle_reindex_restore_handoff_while_session_locked",
        lambda _handoff: events.append("会话结算"),
    )

    def fail_ingress(_context) -> None:
        events.append("入口验收")
        raise RuntimeError("入口失败")

    monkeypatch.setattr(adapters, "default_resume_ingress", fail_ingress)
    monkeypatch.setattr(
        handoff_module,
        "complete_reindex_restore_handoff_while_transition_locked",
        lambda _handoff: pytest.fail("入口失败后不得删除 marker"),
    )

    with maintenance_gate.maintenance_systemd_transition_session():
        with pytest.raises(RuntimeError, match="入口失败"):
            adapters.default_prepare_reindex_handoff(
                _context(tmp_path),
                CodegraphRuntimeIdentity("e" * 32, 0),
            )

    assert events == ["M1边界", "待命准备", "会话结算", "M1边界", "入口验收"]


def test_服务已就绪边界拒绝CodeGraph实例漂移且不误判Webhook状态(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform.ops import reindex_codegraph_resume_adapters as adapters
    from codev_platform.ops import reindex_webhook_maintenance as webhook
    from codev_platform.ops.reindex_codegraph_runtime_identity import (
        CodegraphRuntimeIdentity,
    )

    events: list[str] = []
    expected = CodegraphRuntimeIdentity("e" * 32, 0)
    monkeypatch.setattr(
        adapters,
        "default_codegraph_running_proof",
        lambda _context: events.append("running"),
    )
    monkeypatch.setattr(
        adapters,
        "default_codegraph_identity_reader",
        lambda _context: events.append("identity") or CodegraphRuntimeIdentity("f" * 32, 0),
    )
    monkeypatch.setattr(
        webhook,
        "verify_webhook_maintenance",
        lambda: pytest.fail("实例漂移时不得继续验证 Webhook hold"),
    )

    with pytest.raises(RuntimeError, match="实例身份已变化"):
        adapters._prove_codegraph_and_held_webhook(_context(tmp_path), expected)

    assert events == ["running", "identity"]


def test_M1有效载荷证明保留reindex本地覆盖前先证明Restart_no(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_stage_receipt as receipt
    from codev_platform.ops import reindex_codegraph_resume_adapters as adapters
    from codev_platform.ops import reindex_maintenance

    events: list[str] = []
    bridge = SimpleNamespace(
        dropin_content=b"[Service]\nExecStart=bridge\n",
        exec_start=("/release/venv/bin/python", "-I", "-B"),
    )
    calls: list[
        tuple[object, bool, bool, bytes | None, int | None, bytes | None, tuple[str, ...] | None]
    ] = []
    monkeypatch.setattr(
        reindex_maintenance,
        "prove_reindex_maintenance_dropin",
        lambda: events.append("Restart=no"),
    )
    monkeypatch.setattr(
        receipt,
        "prove_staged_systemd_payload",
        lambda release, *, require_effective, allow_reindex_local_dropins=False, resume_dropin_content=None, codegraph_port=None, codegraph_startup_bridge_dropin_content=None, codegraph_effective_exec_start=None: (
            calls.append(
                (
                    release,
                    require_effective,
                    allow_reindex_local_dropins,
                    resume_dropin_content,
                    codegraph_port,
                    codegraph_startup_bridge_dropin_content,
                    codegraph_effective_exec_start,
                )
            )
        ),
    )
    monkeypatch.setattr(
        adapters,
        "_codegraph_startup_bridge_spec",
        lambda _context: bridge,
    )

    context = _context(tmp_path)
    adapters.default_codegraph_effective_payload_proof(context)

    assert events == ["Restart=no"]
    assert calls == [
        (
            context.runtime_release,
            True,
            True,
            b"[Service]\nEnvironmentFile=\n"
            b"EnvironmentFile=/etc/codev-platform/reindex-codegraph-resume.env\n",
            18091,
            bridge.dropin_content,
            bridge.exec_start,
        )
    ]


def test_桥接适配器始终使用同一上下文派生规格(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform.ops import reindex_codegraph_resume_adapters as adapters
    from codev_platform.ops import reindex_codegraph_startup_bridge_config as bridge_config

    context = _context(tmp_path)
    spec = SimpleNamespace()
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(adapters, "_codegraph_startup_bridge_spec", lambda _context: spec)
    monkeypatch.setattr(
        bridge_config,
        "clear_codegraph_startup_bridge",
        lambda current: events.append(("clear", current)),
    )
    monkeypatch.setattr(
        bridge_config,
        "install_codegraph_startup_bridge",
        lambda current: events.append(("install", current)),
    )
    monkeypatch.setattr(
        bridge_config,
        "remove_codegraph_startup_bridge",
        lambda current: events.append(("remove", current)),
    )

    adapters.default_clear_codegraph_startup_bridge(context)
    adapters.default_install_codegraph_startup_bridge(context)
    adapters.default_remove_codegraph_startup_bridge(context)

    assert events == [("clear", spec), ("install", spec), ("remove", spec)]


def test_锁内M0结算在最终报告前清理bridge并复证维护态(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform.ops import reindex_codegraph_resume_adapters as adapters
    from codev_platform.ops import reindex_combined_maintenance_settlement as settlement

    context = _context(tmp_path)
    events: list[str] = []
    monkeypatch.setattr(
        settlement,
        "settle_combined_maintenance_while_transition_locked",
        lambda: events.append("组合收敛") or SimpleNamespace(proven=True),
    )
    monkeypatch.setattr(
        adapters,
        "default_clear_codegraph_startup_bridge",
        lambda _context: events.append("清理bridge"),
    )
    monkeypatch.setattr(
        adapters,
        "default_inspect_maintenance",
        lambda: events.append("维护复证"),
    )

    assert adapters.default_settle_maintenance_locked(context) is True
    assert events == ["组合收敛", "清理bridge", "维护复证"]
