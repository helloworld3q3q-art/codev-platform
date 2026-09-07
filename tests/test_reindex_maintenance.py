"""reindex 专用 systemd 维护窗口的原子停机与恢复测试。"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from tests.reindex_maintenance_test_support import (
    _dropin,
    _isolate_legacy_reindex_codegraph_boundary as _configure_legacy_reindex_codegraph_boundary,
    _ok,
)


@pytest.fixture(autouse=True)
def _isolate_legacy_reindex_codegraph_boundary(monkeypatch) -> None:
    _configure_legacy_reindex_codegraph_boundary(monkeypatch)


def test_维护默认外部证明必须调用宽写入者扫描(monkeypatch) -> None:
    """prepare、restore 与 status 共用的默认证明不得退回仅 worker 扫描。"""
    from codev_platform.ops import reindex_maintenance as maintenance
    from codev_platform.reindex import external_worker_guard

    events: list[str] = []
    monkeypatch.setattr(
        external_worker_guard,
        "assert_no_external_reindex_workers",
        lambda: pytest.fail("维护默认证明不得只扫描 worker"),
    )
    monkeypatch.setattr(
        external_worker_guard,
        "assert_no_external_reindex_writers",
        lambda: events.append("writers"),
        raising=False,
    )

    maintenance._default_external_worker_proof()

    assert events == ["writers"]


def test_prepare先原子写维护dropin再只停止reindex并证明停机(tmp_path) -> None:
    from codev_platform.ops.reindex_maintenance import prepare_reindex_maintenance

    calls: list[tuple[str, ...]] = []
    events: list[str] = []
    dropin = _dropin(tmp_path)

    prepare_reindex_maintenance(
        platform_name="linux",
        dropin_path=dropin,
        command_runner=lambda command, **_kwargs: calls.append(command) or _ok(),
        gate_activator=lambda: events.append("gate-on"),
        external_worker_proof=lambda: events.append("external"),
        stop_proof=lambda: events.append("proved"),
    )

    assert dropin.read_text(encoding="utf-8") == "[Service]\nRestart=no\n"
    assert calls == [
        ("systemctl", "daemon-reload"),
        ("systemctl", "stop", "codev-reindex.service"),
        ("systemctl", "reset-failed", "codev-reindex.service"),
    ]
    assert events == ["gate-on", "external", "proved"]


def test_prepare在施加CodeGraph维护前持有systemd转换锁(monkeypatch, tmp_path) -> None:
    from codev_platform.ops import reindex_maintenance_prepare as prepare_module
    from codev_platform.ops.reindex_maintenance import prepare_reindex_maintenance

    events: list[str] = []

    @contextmanager
    def gate_lock():
        events.append("transition-enter")
        try:
            yield
        finally:
            events.append("transition-exit")

    @contextmanager
    def transition_intent():
        yield gate_lock

    monkeypatch.setattr(prepare_module, "_codegraph_transition_intent", transition_intent)
    prepare_reindex_maintenance(
        platform_name="linux",
        dropin_path=_dropin(tmp_path),
        command_runner=lambda _command, **_kwargs: _ok(),
        codegraph_prepare=lambda: events.append("codegraph-prepare"),
        codegraph_maintenance_proof=lambda: events.append("codegraph-proof"),
        gate_activator=lambda: events.append("gate"),
        external_worker_proof=lambda: events.append("external"),
        stop_proof=lambda: events.append("stopped"),
    )

    assert events == [
        "gate",
        "transition-enter",
        "codegraph-prepare",
        "transition-exit",
        "external",
        "stopped",
        "codegraph-proof",
    ]


def test_prepare门禁锁忙时预停机证明后重试启用marker(tmp_path) -> None:
    """长任务持共享锁时，只能在受控停机和双重证明后重试独占门禁。"""
    from codev_platform.ops.reindex_maintenance import prepare_reindex_maintenance
    from codev_platform.reindex.maintenance_gate import MaintenanceGateLockBusyError

    dropin = _dropin(tmp_path)
    events: list[str] = []

    def _activate() -> None:
        attempt = events.count("gate") + 1
        events.append("gate")
        if attempt == 1:
            raise MaintenanceGateLockBusyError("维护门禁锁等待超时")

    def _run(command: tuple[str, ...], **_kwargs) -> SimpleNamespace:
        events.append(" ".join(command[1:]))
        return _ok()

    prepare_reindex_maintenance(
        platform_name="linux",
        dropin_path=dropin,
        command_runner=_run,
        gate_activator=_activate,
        external_worker_proof=lambda: events.append("external"),
        stop_proof=lambda: events.append("stopped"),
    )

    assert dropin.read_text(encoding="utf-8") == "[Service]\nRestart=no\n"
    assert events == [
        "gate",
        "daemon-reload",
        "stop codev-reindex.service",
        "reset-failed codev-reindex.service",
        "external",
        "stopped",
        "gate",
        "daemon-reload",
        "stop codev-reindex.service",
        "reset-failed codev-reindex.service",
        "external",
        "stopped",
    ]


def test_prepare仅门禁锁忙才允许预停机重试(tmp_path) -> None:
    """权限或完整性异常不能被错误降级为可安全预停机。"""
    from codev_platform.ops.reindex_maintenance import ReindexMaintenanceError
    from codev_platform.reindex.maintenance_gate import MaintenanceGateError

    from codev_platform.ops import reindex_maintenance as maintenance
    from codev_platform.ops import reindex_maintenance_prepare as prepare_module

    calls: list[tuple[str, ...]] = []

    with pytest.raises(ReindexMaintenanceError, match="无法启用 reindex 维护门禁"):
        prepare_module._activate_gate_with_bounded_preemption(
            maintenance=maintenance,
            activate_gate=lambda: (_ for _ in ()).throw(MaintenanceGateError("权限异常")),
            path=_dropin(tmp_path),
            run=lambda command, **_kwargs: calls.append(command) or _ok(),
            prove_external=lambda: pytest.fail("非锁忙不得预停机"),
            prove_reindex=lambda: pytest.fail("非锁忙不得预停机"),
        )

    assert calls == []


def test_prepare门禁重试失败时最终收敛且不声称安全(tmp_path) -> None:
    """重试 marker 前可能又有新写者，失败出口必须再收敛并拒绝维护写操作。"""
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        prepare_reindex_maintenance,
    )
    from codev_platform.reindex.maintenance_gate import MaintenanceGateLockBusyError

    dropin = _dropin(tmp_path)
    events: list[str] = []

    def _activate() -> None:
        events.append("gate")
        raise MaintenanceGateLockBusyError("维护门禁锁等待超时")

    def _run(command: tuple[str, ...], **_kwargs) -> SimpleNamespace:
        events.append(" ".join(command[1:]))
        return _ok()

    with pytest.raises(ReindexMaintenanceError, match="marker 未启用；安全状态未证明"):
        prepare_reindex_maintenance(
            platform_name="linux",
            dropin_path=dropin,
            command_runner=_run,
            gate_activator=_activate,
            external_worker_proof=lambda: events.append("external"),
            stop_proof=lambda: events.append("stopped"),
        )

    assert events == [
        "gate",
        "daemon-reload",
        "stop codev-reindex.service",
        "reset-failed codev-reindex.service",
        "external",
        "stopped",
        "gate",
        "gate",
        "daemon-reload",
        "stop codev-reindex.service",
        "reset-failed codev-reindex.service",
        "external",
        "stopped",
    ]


def test_prepare门禁重试失败后先补建marker再收敛并证明CodeGraph(
    monkeypatch,
    tmp_path,
) -> None:
    """失败结算补建 marker 成功后，才可证明两个服务都处于安全状态。"""
    from codev_platform.ops import reindex_maintenance_prepare as prepare_module
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        prepare_reindex_maintenance,
    )
    from codev_platform.reindex.maintenance_gate import MaintenanceGateLockBusyError

    events: list[str] = []

    @contextmanager
    def gate_lock():
        events.append("transition-enter")
        try:
            yield
        finally:
            events.append("transition-exit")

    @contextmanager
    def transition_intent():
        yield gate_lock

    def activate_gate() -> None:
        attempt = events.count("gate") + 1
        events.append("gate")
        if attempt <= 2:
            raise MaintenanceGateLockBusyError("维护门禁锁等待超时")

    monkeypatch.setattr(
        prepare_module,
        "_codegraph_transition_intent",
        transition_intent,
    )

    with pytest.raises(ReindexMaintenanceError, match="marker 未启用；已证明安全停机状态"):
        prepare_reindex_maintenance(
            platform_name="linux",
            dropin_path=_dropin(tmp_path),
            command_runner=lambda command, **_kwargs: events.append(" ".join(command[1:])) or _ok(),
            codegraph_prepare=lambda: events.append("codegraph-prepare"),
            codegraph_maintenance_proof=lambda: events.append("codegraph-proof"),
            gate_activator=activate_gate,
            external_worker_proof=lambda: events.append("external"),
            stop_proof=lambda: events.append("reindex-proof"),
        )

    assert events == [
        "gate",
        "daemon-reload",
        "stop codev-reindex.service",
        "reset-failed codev-reindex.service",
        "external",
        "reindex-proof",
        "gate",
        "gate",
        "daemon-reload",
        "stop codev-reindex.service",
        "reset-failed codev-reindex.service",
        "transition-enter",
        "codegraph-prepare",
        "transition-exit",
        "external",
        "reindex-proof",
        "codegraph-proof",
    ]


def test_prepare锁忙预停机失败时仍最终收敛并拒绝维护写操作(
    monkeypatch,
    tmp_path,
) -> None:
    """首轮预停机任一步失败也不能留下可重启 worker 或伪造安全结论。"""
    from codev_platform.ops import reindex_maintenance_prepare as prepare_module
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        prepare_reindex_maintenance,
    )
    from codev_platform.reindex.maintenance_gate import MaintenanceGateLockBusyError

    dropin = _dropin(tmp_path)
    events: list[str] = []
    reloads = 0

    @contextmanager
    def gate_lock():
        events.append("transition-enter")
        try:
            yield
        finally:
            events.append("transition-exit")

    @contextmanager
    def transition_intent():
        yield gate_lock

    def _activate() -> None:
        events.append("gate")
        raise MaintenanceGateLockBusyError("维护门禁锁等待超时")

    def _run(command: tuple[str, ...], **_kwargs) -> SimpleNamespace:
        nonlocal reloads
        events.append(" ".join(command[1:]))
        if command == ("systemctl", "daemon-reload"):
            reloads += 1
            if reloads == 1:
                return SimpleNamespace(returncode=1, stdout="", stderr="敏感错误")
        return _ok()

    monkeypatch.setattr(
        prepare_module,
        "_codegraph_transition_intent",
        transition_intent,
    )

    with pytest.raises(ReindexMaintenanceError, match="安全状态未证明"):
        prepare_reindex_maintenance(
            platform_name="linux",
            dropin_path=dropin,
            command_runner=_run,
            gate_activator=_activate,
            codegraph_prepare=lambda: events.append("codegraph-prepare"),
            codegraph_maintenance_proof=lambda: events.append("codegraph-proof"),
            external_worker_proof=lambda: events.append("external"),
            stop_proof=lambda: events.append("stopped"),
        )

    assert events == [
        "gate",
        "daemon-reload",
        "gate",
        "daemon-reload",
        "stop codev-reindex.service",
        "reset-failed codev-reindex.service",
        "transition-enter",
        "codegraph-prepare",
        "transition-exit",
        "external",
        "stopped",
        "codegraph-proof",
    ]


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit, MemoryError])
def test_维护补偿不吞进程级中断(error_type) -> None:
    """运维人员中断或进程资源异常必须向上层传播，不能伪装成补偿失败。"""
    from codev_platform.ops.reindex_maintenance import _attempt_compensation_action

    def _raise() -> None:
        raise error_type()

    with pytest.raises(error_type):
        _attempt_compensation_action(_raise)


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit, MemoryError])
def test_恢复补偿不吞进程级中断(error_type) -> None:
    """恢复失败后的补偿也必须把进程级中断交还给调用方。"""
    from codev_platform.ops.reindex_maintenance_restore import (
        _attempt_compensation_action,
    )

    def _raise() -> None:
        raise error_type()

    with pytest.raises(error_type):
        _attempt_compensation_action(_raise)


def test_prepare停机失败时保留dropin以维持安全停机状态(tmp_path) -> None:
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        prepare_reindex_maintenance,
    )

    dropin = _dropin(tmp_path)
    calls: list[tuple[str, ...]] = []
    proofs: list[str] = []

    def _run(command: tuple[str, ...], **_kwargs) -> SimpleNamespace:
        calls.append(command)
        return (
            _ok()
            if command[-1] != "codev-reindex.service"
            else SimpleNamespace(returncode=1, stdout="", stderr="敏感错误")
        )

    with pytest.raises(ReindexMaintenanceError):
        prepare_reindex_maintenance(
            platform_name="linux",
            dropin_path=dropin,
            command_runner=_run,
            stop_proof=lambda: proofs.append("stopped"),
            external_worker_proof=lambda: proofs.append("external"),
            gate_activator=lambda: None,
        )

    assert dropin.read_text(encoding="utf-8") == "[Service]\nRestart=no\n"
    assert proofs == ["external", "stopped"]
    assert calls == [
        ("systemctl", "daemon-reload"),
        ("systemctl", "stop", "codev-reindex.service"),
        ("systemctl", "daemon-reload"),
        ("systemctl", "stop", "codev-reindex.service"),
        ("systemctl", "kill", "--kill-who=all", "--signal=SIGKILL", "codev-reindex.service"),
        ("systemctl", "reset-failed", "codev-reindex.service"),
    ]


def test_prepare停机失败仍必须强制收敛并完成双重证明(tmp_path) -> None:
    """marker 已落地后任何 stop 失败都不能把仍可写的旧 worker 留在 cgroup 中。"""
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        prepare_reindex_maintenance,
    )

    dropin = _dropin(tmp_path)
    calls: list[tuple[str, ...]] = []
    proofs: list[str] = []
    stop_attempts = 0

    def _run(command: tuple[str, ...], **_kwargs) -> SimpleNamespace:
        nonlocal stop_attempts
        calls.append(command)
        if command == ("systemctl", "stop", "codev-reindex.service"):
            stop_attempts += 1
            if stop_attempts == 1:
                return SimpleNamespace(returncode=1, stdout="", stderr="敏感错误")
        return _ok()

    with pytest.raises(ReindexMaintenanceError, match="已证明安全停机状态"):
        prepare_reindex_maintenance(
            platform_name="linux",
            dropin_path=dropin,
            command_runner=_run,
            stop_proof=lambda: proofs.append("stopped"),
            external_worker_proof=lambda: proofs.append("external"),
            gate_activator=lambda: None,
        )

    assert dropin.read_text(encoding="utf-8") == "[Service]\nRestart=no\n"
    assert proofs == ["external", "stopped"]
    assert calls == [
        ("systemctl", "daemon-reload"),
        ("systemctl", "stop", "codev-reindex.service"),
        ("systemctl", "daemon-reload"),
        ("systemctl", "stop", "codev-reindex.service"),
        ("systemctl", "reset-failed", "codev-reindex.service"),
    ]


def test_prepare补偿停止仍失败时强制杀死目标cgroup再证明(tmp_path) -> None:
    """stop 超时不能留下旧 worker；只允许对 codev-reindex 的 cgroup 强制收敛。"""
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        prepare_reindex_maintenance,
    )

    dropin = _dropin(tmp_path)
    calls: list[tuple[str, ...]] = []
    proofs: list[str] = []

    def _run(command: tuple[str, ...], **_kwargs) -> SimpleNamespace:
        calls.append(command)
        if command == ("systemctl", "stop", "codev-reindex.service"):
            return SimpleNamespace(returncode=1, stdout="", stderr="敏感错误")
        return _ok()

    with pytest.raises(ReindexMaintenanceError, match="已证明安全停机状态"):
        prepare_reindex_maintenance(
            platform_name="linux",
            dropin_path=dropin,
            command_runner=_run,
            stop_proof=lambda: proofs.append("stopped"),
            external_worker_proof=lambda: proofs.append("external"),
            gate_activator=lambda: None,
        )

    assert calls == [
        ("systemctl", "daemon-reload"),
        ("systemctl", "stop", "codev-reindex.service"),
        ("systemctl", "daemon-reload"),
        ("systemctl", "stop", "codev-reindex.service"),
        ("systemctl", "kill", "--kill-who=all", "--signal=SIGKILL", "codev-reindex.service"),
        ("systemctl", "reset-failed", "codev-reindex.service"),
    ]
    assert proofs == ["external", "stopped"]


def test_prepare发现systemd外worker时保留dropin并拒绝进入迁移窗口(tmp_path) -> None:
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        prepare_reindex_maintenance,
    )

    dropin = _dropin(tmp_path)
    events: list[str] = []
    with pytest.raises(ReindexMaintenanceError):
        prepare_reindex_maintenance(
            platform_name="linux",
            dropin_path=dropin,
            command_runner=lambda _command, **_kwargs: _ok(),
            gate_activator=lambda: None,
            external_worker_proof=lambda: (
                events.append("external") or (_ for _ in ()).throw(RuntimeError("detached worker"))
            ),
            stop_proof=lambda: events.append("stopped"),
        )

    assert dropin.read_text(encoding="utf-8") == "[Service]\nRestart=no\n"
    assert events == ["external", "external", "stopped"]


@pytest.mark.parametrize(
    ("content", "mode", "uid", "gid"),
    [
        (b"[Service]\nRestart=no\n", 0o640, 0, 0),
        (b"[Service]\nRestart=no\n", 0o644, 1000, 0),
        (b"[Service]\nRestart=no\n", 0o644, 0, 1000),
        (b"[Service]\nRestart=yes\n", 0o644, 0, 0),
    ],
)
def test_生产维护dropin必须精确为root属主与0644(
    monkeypatch,
    content: bytes,
    mode: int,
    uid: int,
    gid: int,
) -> None:
    from codev_platform.ops import reindex_codegraph_resume_managed_path as managed
    from codev_platform.ops import reindex_maintenance as module

    monkeypatch.setattr(
        managed,
        "read_optional_root_owned_regular_file_snapshot",
        lambda *_args, **_kwargs: SimpleNamespace(
            content=content,
            mode=mode,
            uid=uid,
            gid=gid,
        ),
    )

    with pytest.raises(module.ReindexMaintenanceError, match="不受信任"):
        module._require_trusted_owned_dropin(module._DROPIN_PATH)


def test_生产维护dropin父目录链或叶子链接不可信时拒绝(monkeypatch) -> None:
    from codev_platform.ops import reindex_codegraph_resume_managed_path as managed
    from codev_platform.ops import reindex_maintenance as module

    def 拒绝路径(*_args, **_kwargs):
        raise managed.TrustedManagedPathError("父目录为链接")

    monkeypatch.setattr(
        managed,
        "read_optional_root_owned_regular_file_snapshot",
        拒绝路径,
    )

    with pytest.raises(module.ReindexMaintenanceError, match="不受信任"):
        module._require_trusted_owned_dropin(module._DROPIN_PATH)


def test_生产维护dropin写入委派root可信dirfd并立即复证(monkeypatch, tmp_path) -> None:
    from codev_platform.ops import reindex_codegraph_resume_managed_path as managed
    from codev_platform.ops import reindex_maintenance as module

    dropin = _dropin(tmp_path)
    events: list[object] = []
    monkeypatch.setattr(module, "_DROPIN_PATH", dropin)
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(
        managed,
        "write_root_owned_regular_file_atomic",
        lambda path, content, *, mode, uid, gid: events.append(
            ("trusted-write", path, content, mode, uid, gid)
        ),
    )
    monkeypatch.setattr(
        module,
        "_require_owned_dropin",
        lambda path: events.append(("proof", path)),
    )

    module._write_dropin(dropin)

    assert events == [
        (
            "trusted-write",
            dropin,
            b"[Service]\nRestart=no\n",
            0o644,
            0,
            0,
        ),
        ("proof", dropin),
    ]


@pytest.mark.parametrize("reason", ("父目录不受信任", "叶子是符号链接"))
def test_生产维护dropin写入遇不可信目录或链接时禁止回退可移植写法(
    monkeypatch,
    tmp_path,
    reason: str,
) -> None:
    from codev_platform.ops import reindex_codegraph_resume_managed_path as managed
    from codev_platform.ops import reindex_maintenance as module

    dropin = _dropin(tmp_path)
    monkeypatch.setattr(module, "_DROPIN_PATH", dropin)
    monkeypatch.setattr(module.sys, "platform", "linux")

    def reject(*_args, **_kwargs):
        raise managed.TrustedManagedPathError(reason)

    monkeypatch.setattr(managed, "write_root_owned_regular_file_atomic", reject)

    with pytest.raises(module.ReindexMaintenanceError, match="无法写入"):
        module._write_dropin(dropin)


def test_prepare在reload后发现dropin被替换会补偿并拒绝成功(tmp_path) -> None:
    from codev_platform.ops import reindex_maintenance as module

    dropin = _dropin(tmp_path)
    reload_count = 0

    def run(command: tuple[str, ...], **_kwargs):
        nonlocal reload_count
        if command == ("systemctl", "daemon-reload"):
            reload_count += 1
            if reload_count == 1:
                dropin.write_text("[Service]\nRestart=always\n", encoding="utf-8")
        return _ok()

    with pytest.raises(module.ReindexMaintenanceError, match="已证明安全停机状态"):
        module.prepare_reindex_maintenance(
            platform_name="linux",
            dropin_path=dropin,
            command_runner=run,
            gate_activator=lambda: None,
            external_worker_proof=lambda: None,
            stop_proof=lambda: None,
        )

    assert reload_count == 2
    assert dropin.read_text(encoding="utf-8") == "[Service]\nRestart=no\n"


def test_安全dropin补偿在reload后再次复证(monkeypatch, tmp_path) -> None:
    from codev_platform.ops import reindex_maintenance as module

    dropin = _dropin(tmp_path)
    events: list[object] = []
    monkeypatch.setattr(
        module,
        "_write_dropin",
        lambda path: events.append(("write", path)),
    )
    monkeypatch.setattr(
        module,
        "_require_owned_dropin",
        lambda path: events.append(("proof", path)),
    )

    module._restore_safety_dropin(
        dropin,
        lambda command, **_kwargs: events.append(command) or _ok(),
    )

    assert events == [
        ("write", dropin),
        ("systemctl", "daemon-reload"),
        ("proof", dropin),
    ]
