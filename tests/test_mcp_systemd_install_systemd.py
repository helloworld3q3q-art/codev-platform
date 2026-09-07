"""全量 systemd 安装 Linux 适配器测试。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform.mcp_systemd_install_contract import SystemdUnitProcessState


def test_默认状态读取拒绝无法由事务精确恢复的状态(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """static、indirect、failed 等状态不能被错误压缩成布尔值。"""
    from codev_platform import mcp_systemd_install_systemd as module

    states = iter(("static", "inactive"))
    monkeypatch.setattr(module, "_read_systemctl_state", lambda _command: next(states))

    with pytest.raises(module.SystemdInstallTransactionError, match="不可逆"):
        module.default_unit_state_reader("codev-mcp-codegraph.service")


def test_不存在unit的unknown活动态规范化为可恢复的inactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """systemctl 对未加载 unit 可报告 unknown，安装前应安全归一为 not-found/inactive。"""
    from codev_platform import mcp_systemd_install_systemd as module

    states = iter(("not-found", "unknown"))
    monkeypatch.setattr(module, "_read_systemctl_state", lambda _command: next(states))
    monkeypatch.setattr(
        module,
        "read_persistent_unit_enablement_state",
        lambda _name: "disabled",
    )

    assert module.default_unit_state_reader(
        "codev-mcp-codegraph.service"
    ) == module.SystemdUnitState(
        unit_file_state="not-found",
        active_state="inactive",
    )


def test_runtime_mask期间按持久链接读取底层enabled状态(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """runtime mask 只负责阻止启动，不得覆盖事务需要恢复的持久启用态。"""
    from codev_platform import mcp_systemd_install_systemd as module

    states = iter(("masked-runtime", "inactive"))
    monkeypatch.setattr(module, "_read_systemctl_state", lambda _command: next(states))
    monkeypatch.setattr(
        module,
        "read_persistent_unit_enablement_state",
        lambda name: "enabled" if name == "codev-mcp-codegraph.service" else "disabled",
        raising=False,
    )

    assert module.default_unit_state_reader(
        "codev-mcp-codegraph.service"
    ) == module.SystemdUnitState(
        unit_file_state="enabled",
        active_state="inactive",
    )


def test_默认启用态证明只读取持久链接(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module

    calls: list[str] = []
    monkeypatch.setattr(
        module,
        "read_persistent_unit_enablement_state",
        lambda name: calls.append(name) or "enabled",
    )

    assert module.default_unit_enablement_state_reader("codev-web.service") == "enabled"
    assert calls == ["codev-web.service"]
    assert (
        module.default_ports().read_unit_enablement_state
        is module.default_unit_enablement_state_reader
    )


def test_默认unit原像恢复保留内容权限与完整属主(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Linux 适配器不得把回滚原像固定重写为 0644/root:root。"""
    from codev_platform import mcp_systemd_install_systemd as module
    from codev_platform import mcp_systemd_install_filesystem as filesystem
    from codev_platform.ops import reindex_codegraph_resume_managed_path as managed

    legacy_root = tmp_path / "etc" / "systemd" / "system"
    target_root = tmp_path / "usr" / "local" / "lib" / "systemd" / "system"
    monkeypatch.setattr(filesystem, "_LEGACY_SYSTEMD_UNIT_DIRECTORY", legacy_root)
    monkeypatch.setattr(filesystem, "_CANONICAL_SYSTEMD_UNIT_DIRECTORY", target_root)
    source_snapshot = managed.RootOwnedRegularFileSnapshot(
        content=b"[Service]\nEnvironment=SECRET=kept\n",
        mode=0o640,
        uid=0,
        gid=246,
    )
    calls: list[object] = []
    monkeypatch.setattr(
        managed,
        "read_optional_root_owned_regular_file_snapshot",
        lambda path, *, max_bytes: (
            calls.append(("read", path, max_bytes))
            or (None if path == legacy_root / "codev-mcp-codegraph.service" else source_snapshot)
        ),
    )
    monkeypatch.setattr(
        managed,
        "write_root_owned_regular_file_atomic",
        lambda path, content, *, mode, uid, gid: calls.append(
            ("write", path, content, mode, uid, gid)
        ),
    )

    snapshot = module.default_unit_snapshot_reader("codev-mcp-codegraph.service")
    assert snapshot == module.SystemdUnitFileSnapshot(
        content=source_snapshot.content,
        mode=0o640,
        uid=0,
        gid=246,
    )
    module.default_unit_restorer("codev-mcp-codegraph.service", snapshot)

    assert calls[-1] == (
        "write",
        target_root / "codev-mcp-codegraph.service",
        source_snapshot.content,
        0o640,
        0,
        246,
    )


def test_CodeGraph主unit改用canonical目录且legacy影子会在写入前拒绝(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """全量安装不得静默写入被 /etc 主文件遮蔽的 canonical unit。"""
    from codev_platform import mcp_systemd_install_systemd as module
    from codev_platform import mcp_systemd_install_filesystem as filesystem
    from codev_platform.ops import reindex_codegraph_resume_managed_path as managed

    legacy_root = tmp_path / "etc" / "systemd" / "system"
    canonical_root = tmp_path / "usr" / "local" / "lib" / "systemd" / "system"
    legacy_snapshot = managed.RootOwnedRegularFileSnapshot(
        content=b"[Service]\nExecStart=/legacy\n",
        mode=0o644,
        uid=0,
        gid=0,
    )
    reads: list[object] = []
    monkeypatch.setattr(filesystem, "_LEGACY_SYSTEMD_UNIT_DIRECTORY", legacy_root)
    monkeypatch.setattr(filesystem, "_CANONICAL_SYSTEMD_UNIT_DIRECTORY", canonical_root)
    monkeypatch.setattr(
        managed,
        "read_optional_root_owned_regular_file_snapshot",
        lambda path, *, max_bytes: (
            reads.append((path, max_bytes))
            or (legacy_snapshot if path == legacy_root / "codev-mcp-codegraph.service" else None)
        ),
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="迁移"):
        module.default_unit_snapshot_reader("codev-mcp-codegraph.service")

    assert reads == [(legacy_root / "codev-mcp-codegraph.service", filesystem._MAX_UNIT_BYTES)]


def test_安装器以systemd实际解析而非run文件存在性判断mask(monkeypatch: pytest.MonkeyPatch) -> None:
    """/run 残留但 /etc 仍生效时，安装器必须明确拒绝而非误认为可继续。"""
    from codev_platform import mcp_systemd_install_systemd as module
    from codev_platform.core.systemd_unit_resolution import SystemdUnitResolution

    monkeypatch.setattr(
        module,
        "read_unit_resolution",
        lambda _name: SystemdUnitResolution(
            unit_name="codev-mcp-codegraph.service",
            load_state="loaded",
            unit_file_state="enabled",
            fragment_path="/etc/systemd/system/codev-mcp-codegraph.service",
        ),
    )
    monkeypatch.setattr(module, "read_runtime_mask_target", lambda _name: "/dev/null")

    with pytest.raises(module.SystemdInstallTransactionError, match="无效 runtime mask 残留"):
        module.default_runtime_mask_proof()


def test_默认安装锁在独占区拒绝维护marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """维护标记检查必须发生在转换锁内，且激活状态一律失败关闭。"""
    from contextlib import contextmanager

    from codev_platform import mcp_systemd_install_systemd as module
    from codev_platform.reindex import maintenance_gate

    events: list[str] = []

    @contextmanager
    def 独占转换锁():
        events.append("进入独占锁")
        try:
            yield
        finally:
            events.append("退出独占锁")

    def 维护中() -> bool:
        events.append("检查维护标记")
        return True

    monkeypatch.setattr(maintenance_gate, "maintenance_systemd_transition_lock", 独占转换锁)
    monkeypatch.setattr(maintenance_gate, "maintenance_gate_active", 维护中)

    with pytest.raises(module.SystemdInstallTransactionError, match="维护"):
        with module.default_installer_lock():
            raise AssertionError("维护 marker 已激活时不得进入事务")

    assert events == ["进入独占锁", "检查维护标记", "退出独占锁"]


def test_默认安装锁不得把事务体异常伪装成取锁失败(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """锁包装器只负责锁与维护标记，事务体异常必须保持原样。"""
    from contextlib import contextmanager

    from codev_platform import mcp_systemd_install_systemd as module
    from codev_platform.reindex import maintenance_gate

    @contextmanager
    def 独占转换锁():
        yield

    monkeypatch.setattr(maintenance_gate, "maintenance_systemd_transition_lock", 独占转换锁)
    monkeypatch.setattr(maintenance_gate, "maintenance_gate_active", lambda: False)

    with pytest.raises(RuntimeError, match="事务体失败"):
        with module.default_installer_lock():
            raise RuntimeError("事务体失败")


def test_默认安装锁在事务体异常后保留转换锁退出异常(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """事务体与锁退出均异常时，退出异常不能被包装为取锁失败。"""
    from codev_platform import mcp_systemd_install_systemd as module
    from codev_platform.reindex import maintenance_gate

    class 退出失败转换锁:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *_args: object) -> bool:
            raise RuntimeError("转换锁退出失败")

    monkeypatch.setattr(
        maintenance_gate,
        "maintenance_systemd_transition_lock",
        lambda: 退出失败转换锁(),
    )
    monkeypatch.setattr(maintenance_gate, "maintenance_gate_active", lambda: False)

    with pytest.raises(RuntimeError, match="转换锁退出失败"):
        with module.default_installer_lock():
            raise RuntimeError("事务体失败")


def test_install_only锁只串行管理员会话且不获取worker_gate排他锁(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install-only 不改变进程活动态，不能让长事务阻塞 reindex 写许可。"""
    from contextlib import contextmanager

    from codev_platform import mcp_systemd_install_systemd as module
    from codev_platform.reindex import maintenance_gate

    events: list[str] = []
    inside_session = False

    @contextmanager
    def 管理员会话():
        nonlocal inside_session
        inside_session = True
        events.append("进入管理员会话")
        try:
            yield
        finally:
            events.append("退出管理员会话")
            inside_session = False

    @contextmanager
    def 禁止完整转换锁():
        raise AssertionError("install-only 不得阻断 worker gate")
        yield

    def 证明运行模式():
        assert inside_session is True
        events.append("证明运行模式")
        return module.RuntimeMaskState.UNMASKED

    monkeypatch.setattr(
        maintenance_gate,
        "maintenance_systemd_transition_session",
        管理员会话,
    )
    monkeypatch.setattr(
        maintenance_gate,
        "maintenance_systemd_transition_lock",
        禁止完整转换锁,
    )
    monkeypatch.setattr(module, "_install_only_runtime_mode", 证明运行模式)

    with module.default_install_only_installer_lock():
        events.append("事务体")

    assert events == [
        "进入管理员会话",
        "证明运行模式",
        "事务体",
        "证明运行模式",
        "退出管理员会话",
    ]


def test_install_only锁保留事务体与会话退出异常(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module
    from codev_platform.reindex import maintenance_gate

    class 退出失败会话:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *_args: object) -> bool:
            raise RuntimeError("管理员会话退出失败")

    monkeypatch.setattr(
        maintenance_gate,
        "maintenance_systemd_transition_session",
        lambda: 退出失败会话(),
    )
    monkeypatch.setattr(
        module,
        "_install_only_runtime_mode",
        lambda: module.RuntimeMaskState.UNMASKED,
    )

    with pytest.raises(RuntimeError, match="管理员会话退出失败"):
        with module.default_install_only_installer_lock():
            raise RuntimeError("事务体失败")


def test_install_only锁内运行模式漂移仍然失败关闭(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import nullcontext

    from codev_platform import mcp_systemd_install_systemd as module
    from codev_platform.reindex import maintenance_gate

    modes = iter(
        (
            module.RuntimeMaskState.UNMASKED,
            module.RuntimeMaskState.EFFECTIVE_RUNTIME_MASK,
        )
    )
    monkeypatch.setattr(
        maintenance_gate,
        "maintenance_systemd_transition_session",
        nullcontext,
    )
    monkeypatch.setattr(module, "_install_only_runtime_mode", lambda: next(modes))

    with pytest.raises(module.SystemdInstallTransactionError, match="模式发生漂移"):
        with module.default_install_only_installer_lock():
            pass


def test_guarded_stage继续使用完整维护转换锁() -> None:
    from codev_platform import mcp_systemd_install_systemd as module

    install_only = module.default_install_only_ports()
    guarded_stage = module.default_guarded_stage_ports(lambda: None)

    assert install_only.installer_lock is module.default_install_only_installer_lock
    assert guarded_stage.installer_lock is module.default_maintenance_stage_installer_lock


def test_维护态安装锁在同一排他区前后证明完整维护稳态(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """marker、停机和 mask 证明不能落在排他锁外形成 TOCTOU。"""
    from contextlib import contextmanager

    from codev_platform import mcp_systemd_install_systemd as module
    from codev_platform.ops import reindex_maintenance
    from codev_platform.reindex import maintenance_gate

    events: list[str] = []
    inside = False

    @contextmanager
    def 独占转换锁():
        nonlocal inside
        inside = True
        events.append("进入独占锁")
        try:
            yield
        finally:
            events.append("退出独占锁")
            inside = False

    def 证明维护稳态() -> None:
        assert inside is True
        events.append("证明维护稳态")

    monkeypatch.setattr(
        maintenance_gate,
        "maintenance_systemd_transition_lock",
        独占转换锁,
    )
    monkeypatch.setattr(
        reindex_maintenance,
        "inspect_reindex_maintenance",
        证明维护稳态,
    )
    monkeypatch.setattr(
        module,
        "default_unit_process_state_reader",
        lambda _name: SystemdUnitProcessState("inactive", 0, ""),
    )

    with module.default_maintenance_stage_installer_lock():
        events.append("事务体")

    assert events == [
        "进入独占锁",
        "证明维护稳态",
        "事务体",
        "证明维护稳态",
        "退出独占锁",
    ]


def test_维护态安装锁在事务补偿后证明失败时不得保留原始成功语义(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """事务体失败后若维护稳态也丢失，调用方必须得到安全状态未证明。"""
    from contextlib import contextmanager

    from codev_platform import mcp_systemd_install_systemd as module
    from codev_platform.ops import reindex_maintenance
    from codev_platform.reindex import maintenance_gate

    calls = 0

    @contextmanager
    def 独占转换锁():
        yield

    def 证明维护稳态() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise reindex_maintenance.ReindexMaintenanceError("维护稳态丢失")

    monkeypatch.setattr(
        maintenance_gate,
        "maintenance_systemd_transition_lock",
        独占转换锁,
    )
    monkeypatch.setattr(
        reindex_maintenance,
        "inspect_reindex_maintenance",
        证明维护稳态,
    )
    monkeypatch.setattr(
        module,
        "default_unit_process_state_reader",
        lambda _name: SystemdUnitProcessState("inactive", 0, ""),
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="维护稳态"):
        with module.default_maintenance_stage_installer_lock():
            raise RuntimeError("事务体失败")


def test_维护态证明拒绝仍在接收推送的Webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module
    from codev_platform.ops import reindex_maintenance

    monkeypatch.setattr(reindex_maintenance, "inspect_reindex_maintenance", lambda: None)
    monkeypatch.setattr(
        module,
        "default_unit_process_state_reader",
        lambda _name: SystemdUnitProcessState("active", 4321, "1" * 32),
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="Webhook 已停止"):
        module.default_maintenance_stage_runtime_proof()


def test_维护态载荷证明只把runtime_mask下的CodeGraph降级为落盘证明(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """runtime mask 下不能把 canonical 落盘原像误当成有效解析证明。"""
    import hashlib

    from codev_platform import mcp_systemd_install_systemd as module

    content = b"[Service]\nExecStart=/usr/bin/true\n"

    def 载荷(name: str):
        spec = module.SystemdUnitInstallSpec(
            source=(tmp_path / name).resolve(),
            content_digest=hashlib.sha256(content).hexdigest(),
            enable=True,
            restart=True,
            activation_mode=(
                module.SystemdUnitActivationMode.REINDEX_STATE_MACHINE
                if name == "codev-reindex.service"
                else module.SystemdUnitActivationMode.CODEGRAPH_STATE_MACHINE
                if name == "codev-mcp-codegraph.service"
                else module.SystemdUnitActivationMode.INGRESS_STATE_MACHINE
                if name == "codev-webhook.service"
                else module.SystemdUnitActivationMode.STANDARD
            ),
        )
        return module.SystemdUnitPayload(spec, content)

    payloads = (
        载荷("codev-mcp-codegraph.service"),
        载荷("codev-reindex.service"),
        载荷("codev-webhook.service"),
        载荷("codev-web.service"),
    )
    effective: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        module,
        "default_effective_unit_payload_verifier",
        lambda items: effective.append(tuple(item.spec.unit_name for item in items)),
    )
    monkeypatch.setattr(
        module,
        "default_unit_snapshot_reader",
        lambda _name: module.SystemdUnitFileSnapshot(content, 0o644, 0, 0),
    )
    module.default_maintenance_stage_unit_payload_verifier(payloads)

    assert effective == [("codev-reindex.service", "codev-webhook.service", "codev-web.service")]


def test_维护态保护unit落盘权限或内容不精确时失败关闭(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import hashlib

    from codev_platform import mcp_systemd_install_systemd as module

    content = b"[Service]\nExecStart=/usr/bin/true\n"

    def 载荷(name: str):
        return module.SystemdUnitPayload(
            module.SystemdUnitInstallSpec(
                source=(tmp_path / name).resolve(),
                content_digest=hashlib.sha256(content).hexdigest(),
                enable=True,
                restart=True,
                activation_mode=(
                    module.SystemdUnitActivationMode.REINDEX_STATE_MACHINE
                    if name == "codev-reindex.service"
                    else module.SystemdUnitActivationMode.CODEGRAPH_STATE_MACHINE
                    if name == "codev-mcp-codegraph.service"
                    else module.SystemdUnitActivationMode.INGRESS_STATE_MACHINE
                    if name == "codev-webhook.service"
                    else module.SystemdUnitActivationMode.STANDARD
                ),
            ),
            content,
        )

    payloads = (
        载荷("codev-mcp-codegraph.service"),
        载荷("codev-reindex.service"),
        载荷("codev-webhook.service"),
    )
    monkeypatch.setattr(module, "default_effective_unit_payload_verifier", lambda _items: None)
    monkeypatch.setattr(
        module,
        "default_unit_snapshot_reader",
        lambda name: module.SystemdUnitFileSnapshot(
            content,
            0o640 if name == "codev-mcp-codegraph.service" else 0o644,
            0,
            0,
        ),
    )
    monkeypatch.setattr(
        module,
        "_run_systemctl_command",
        lambda _command: SimpleNamespace(
            returncode=0,
            stdout=(
                "FragmentPath=/etc/systemd/system/codev-reindex.service\n"
                "DropInPaths=\n"
                "ExecStart={ path=/usr/bin/true ; argv[]=/usr/bin/true ; "
                "ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; "
                "code=(null) ; status=0/0 }\n"
            ),
        ),
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="落盘载荷未证明"):
        module.default_maintenance_stage_unit_payload_verifier(payloads)


def test_维护态reindex被run目录高优先级主unit遮蔽时拒绝(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module

    content = b"[Service]\nExecStart=/usr/bin/true\n"

    def 载荷(name: str):
        return module.SystemdUnitPayload(
            module.SystemdUnitInstallSpec(
                source=(tmp_path / name).resolve(),
                content_digest=hashlib.sha256(content).hexdigest(),
                enable=True,
                restart=True,
                activation_mode=(
                    module.SystemdUnitActivationMode.REINDEX_STATE_MACHINE
                    if name == "codev-reindex.service"
                    else module.SystemdUnitActivationMode.CODEGRAPH_STATE_MACHINE
                    if name == "codev-mcp-codegraph.service"
                    else module.SystemdUnitActivationMode.INGRESS_STATE_MACHINE
                    if name == "codev-webhook.service"
                    else module.SystemdUnitActivationMode.STANDARD
                ),
            ),
            content,
        )

    payloads = (
        载荷("codev-mcp-codegraph.service"),
        载荷("codev-reindex.service"),
        载荷("codev-webhook.service"),
    )
    monkeypatch.setattr(
        module,
        "default_unit_snapshot_reader",
        lambda _name: module.SystemdUnitFileSnapshot(content, 0o644, 0, 0),
    )
    monkeypatch.setattr(
        module,
        "_run_systemctl_command",
        lambda _command: SimpleNamespace(
            returncode=0,
            stdout=(
                "FragmentPath=/run/systemd/system/codev-reindex.service\n"
                "DropInPaths=\n"
                "ExecStart={ path=/usr/bin/true ; argv[]=/usr/bin/true ; "
                "ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; "
                "code=(null) ; status=0/0 }\n"
            ),
        ),
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="FragmentPath"):
        module.default_maintenance_stage_unit_payload_verifier(payloads)


@pytest.mark.parametrize("exception_type", (OSError, TypeError, ValueError))
def test_默认安装锁保留维护转换锁内的事务体异常(
    exception_type: type[Exception],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """真实转换锁不得将事务体的普通异常改写为门禁错误。"""
    from contextlib import contextmanager

    from codev_platform import mcp_systemd_install_systemd as module
    from codev_platform.reindex import maintenance_gate

    @contextmanager
    def 可用独占锁():
        yield

    monkeypatch.setattr(
        maintenance_gate,
        "maintenance_systemd_transition_lock",
        可用独占锁,
    )
    monkeypatch.setattr(maintenance_gate, "maintenance_gate_active", lambda: False)
    body_error = exception_type("事务体失败")

    with pytest.raises(exception_type) as captured:
        with module.default_installer_lock():
            raise body_error

    assert captured.value is body_error
