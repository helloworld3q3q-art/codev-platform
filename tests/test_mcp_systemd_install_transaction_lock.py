"""systemd 安装事务的独占安装锁边界测试。"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import replace
from pathlib import Path
from threading import Event, Lock, Thread

import pytest

from tests.mcp_systemd_install_transaction_support import _清单, _端口


def test_独占安装锁阻止并发事务在补偿期间读取或写入(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """T1 失败补偿未完成前，T2 不能取得原像或覆盖受管单元。"""
    import hashlib

    from codev_platform import mcp_systemd_install_transaction as module

    class 无信号守卫:
        pending = False

        def arm(self) -> None:
            return None

        def raise_if_pending(self) -> None:
            return None

        def close(self) -> None:
            return None

        def make_interrupt(self) -> KeyboardInterrupt:
            return KeyboardInterrupt()

    monkeypatch.setattr(module, "_DeferredSigintGuard", 无信号守卫)
    unit_name = "codev-web.service"
    source = (tmp_path / unit_name).resolve()
    初始内容 = b"[Service]\nExecStart=/usr/bin/initial\n"
    T1内容 = b"[Service]\nExecStart=/usr/bin/t1\n"
    T2内容 = b"[Service]\nExecStart=/usr/bin/t2\n"
    files = {
        unit_name: module.SystemdUnitFileSnapshot(
            content=初始内容,
            mode=0o640,
            uid=0,
            gid=123,
        )
    }
    states = {unit_name: module.SystemdUnitState("disabled", "inactive")}
    events: list[tuple[str, str, str | None]] = []
    lock = Lock()
    T1补偿已到达 = Event()
    放行T1补偿 = Event()
    T2正在等待安装锁 = Event()
    异常: list[BaseException] = []
    T2报告: list[object] = []

    def 创建清单(content: bytes):
        unit = module.SystemdUnitInstallSpec(
            source=source,
            content_digest=hashlib.sha256(content).hexdigest(),
            enable=True,
            restart=True,
        )
        return module.SystemdInstallManifest(
            units=(unit,),
            runtime_revision="1" * 40,
        )

    def 创建端口(label: str, content: bytes, *, 失败: bool):
        from codev_platform.mcp_systemd_release_shadow import LegacyReleaseDropInSnapshot

        @contextmanager
        def 安装锁():
            if lock.locked():
                T2正在等待安装锁.set()
            if not lock.acquire(timeout=3):
                raise RuntimeError("安装锁测试超时")
            events.append((label, "进入安装锁", None))
            try:
                yield
            finally:
                events.append((label, "退出安装锁", None))
                lock.release()

        def 读取原像(name: str):
            events.append((label, "读取原像", name))
            return files[name]

        def 写入(name: str, payload: bytes) -> None:
            events.append((label, "写入", name))
            files[name] = module.SystemdUnitFileSnapshot(
                content=payload,
                mode=0o644,
                uid=0,
                gid=0,
            )

        def 恢复文件(name: str, snapshot) -> None:
            events.append((label, "恢复文件", name))
            if label == "T1":
                T1补偿已到达.set()
                if not 放行T1补偿.wait(timeout=3):
                    raise RuntimeError("未放行 T1 补偿")
            files[name] = snapshot

        def 读取状态(name: str):
            events.append((label, "读取状态", name))
            return states[name]

        def 恢复启用态(name: str, state) -> None:
            states[name] = module.SystemdUnitState(
                state.unit_file_state,
                states[name].active_state,
            )

        def 恢复活动态(name: str, state) -> None:
            states[name] = module.SystemdUnitState(
                states[name].unit_file_state,
                state.active_state,
            )

        def 执行命令(command: tuple[str, ...]) -> None:
            action = command[1]
            if 失败 and action == "daemon-reload":
                raise RuntimeError("T1 触发失败")
            for name in command[2:]:
                if action == "enable":
                    states[name] = module.SystemdUnitState("enabled", states[name].active_state)
                elif action == "restart":
                    states[name] = module.SystemdUnitState(
                        states[name].unit_file_state,
                        "active",
                    )

        def 读取shadow原像(names: tuple[str, ...]):
            return tuple(
                LegacyReleaseDropInSnapshot(
                    unit_name=name,
                    active_path=Path(f"/etc/systemd/system/{name}.d/90-codev-release.conf"),
                    archive_path=Path(
                        f"/etc/systemd/system/{name}.d/90-codev-release.conf.codev-retired"
                    ),
                    active_snapshot=None,
                    archive_snapshot=None,
                )
                for name in names
            )

        return module.SystemdInstallPorts(
            provision_maintenance_gate=lambda: None,
            installer_lock=安装锁,
            runtime_binding_lock=lambda _manifest: nullcontext(object()),
            verify_runtime_binding=lambda _manifest, _bound: None,
            verify_target_user_preflight=lambda _manifest, _payloads, _bound: None,
            verify_install_boundary=lambda: None,
            read_unit_source=lambda _source: content,
            snapshot_legacy_release_dropins=读取shadow原像,
            retire_legacy_release_dropins=lambda _snapshots: None,
            restore_legacy_release_dropins=lambda _snapshots: None,
            verify_legacy_release_dropins_retired=lambda _snapshots: None,
            verify_managed_install_contract=lambda _manifest, _payloads: None,
            verify_effective_unit_payloads=lambda _payloads: None,
            snapshot_installed_unit=读取原像,
            write_installed_unit=写入,
            restore_installed_unit=恢复文件,
            read_unit_state=读取状态,
            read_unit_enablement_state=lambda name: states[name].unit_file_state,
            read_unit_process_state=lambda _name: module.SystemdUnitProcessState("inactive", 0, ""),
            restore_unit_file_state=恢复启用态,
            restore_unit_activity_state=恢复活动态,
            systemctl=执行命令,
            verify_running_units=lambda _names: None,
            read_stage_runtime_identity=lambda: (_ for _ in ()).throw(
                AssertionError("普通安装不得读取 stage 发布身份")
            ),
            snapshot_stage_receipt=lambda: None,
            write_stage_receipt=lambda _content: None,
            restore_stage_receipt=lambda _snapshot: None,
            verify_stage_receipt=lambda _content: None,
        )

    T1清单 = 创建清单(T1内容)
    T2清单 = 创建清单(T2内容)
    T1端口 = 创建端口("T1", T1内容, 失败=True)
    T2端口 = 创建端口("T2", T2内容, 失败=False)

    def 执行T1() -> None:
        try:
            module.install_systemd_transaction(
                T1清单,
                ports=T1端口,
                platform_name="linux",
                effective_user_id=lambda: 0,
            )
        except BaseException as error:
            异常.append(error)

    def 执行T2() -> None:
        try:
            T2报告.append(
                module.install_systemd_transaction(
                    T2清单,
                    ports=T2端口,
                    platform_name="linux",
                    effective_user_id=lambda: 0,
                )
            )
        except BaseException as error:
            异常.append(error)

    T1线程 = Thread(target=执行T1)
    T2线程 = Thread(target=执行T2)
    T1线程.start()
    assert T1补偿已到达.wait(timeout=2)
    T2线程.start()
    try:
        assert T2正在等待安装锁.wait(timeout=2)
        assert not any(event[0] == "T2" and event[1] in {"读取原像", "写入"} for event in events)
    finally:
        放行T1补偿.set()
        T1线程.join(timeout=3)
        T2线程.join(timeout=3)

    assert not T1线程.is_alive()
    assert not T2线程.is_alive()
    assert len(异常) == 1
    assert isinstance(异常[0], module.SystemdInstallTransactionError)
    assert len(T2报告) == 1
    assert files[unit_name].content == T2内容


def test_安装锁退出异常后不得重取锁并按旧原像补偿(tmp_path: Path) -> None:
    """锁退出后所有权无法证明时只能失败关闭，不能重入并覆盖后续事务。"""
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    ports, files, _states, initial_files, _initial_states = _端口(module, manifest, events)
    factory_calls = 0

    class 退出失败安装锁:
        def __enter__(self) -> None:
            events.append("进入安装锁")
            return None

        def __exit__(self, *_args: object) -> bool:
            events.append("退出安装锁")
            raise RuntimeError("安装锁退出失败")

    def 创建安装锁() -> 退出失败安装锁:
        nonlocal factory_calls
        factory_calls += 1
        return 退出失败安装锁()

    ports = replace(ports, installer_lock=创建安装锁)
    with pytest.raises(module.SystemdInstallTransactionError, match="安全状态未证明"):
        module.install_systemd_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert factory_calls == 1
    assert files != initial_files
    assert not any(isinstance(event, tuple) and event[0].startswith("恢复") for event in events)
