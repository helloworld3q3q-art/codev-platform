"""固定 systemd 主 unit 布局迁移的回归测试。"""

from __future__ import annotations

import hashlib
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

import pytest


def _安装输入(module, tmp_path: Path):
    from codev_platform.mcp_systemd_install_contract import (
        SystemdInstallManifest,
        SystemdUnitActivationMode,
        SystemdUnitInstallSpec,
        SystemdUnitPayload,
    )
    from codev_platform.mcp_systemd_install_input import VerifiedInstallInput

    contents = {
        "codev-mcp-codegraph.service": (
            b"[Service]\nExecStart=/new/python -I -m codev_platform.codegraph.server "
            b"--http --port 19091\n"
        ),
        "codev-reindex.service": b"[Service]\nExecStart=/new/reindex\n",
    }
    specs = tuple(
        SystemdUnitInstallSpec(
            source=(tmp_path / name).resolve(),
            content_digest=hashlib.sha256(content).hexdigest(),
            enable=True,
            restart=True,
            activation_mode=(
                SystemdUnitActivationMode.CODEGRAPH_STATE_MACHINE
                if name == "codev-mcp-codegraph.service"
                else SystemdUnitActivationMode.REINDEX_STATE_MACHINE
            ),
        )
        for name, content in contents.items()
    )
    payloads = tuple(
        SystemdUnitPayload(spec=spec, content=contents[spec.unit_name]) for spec in specs
    )
    return VerifiedInstallInput(
        manifest=SystemdInstallManifest(specs, runtime_revision="1" * 40),
        payloads=payloads,
    )


def _运行态(module, *, fragment: Path, active: str, invocation: str):
    return module.SystemdUnitRuntime(
        fragment_path=str(fragment),
        unit_file_state="enabled",
        active_state=active,
        sub_state="running" if active == "active" else "failed",
        result="success" if active == "active" else "exit-code",
        exec_main_code="exited",
        exec_main_status="0" if active == "active" else "77",
        invocation_id=invocation,
    )


class _模拟端口:
    def __init__(self, module, tmp_path: Path) -> None:
        from codev_platform.ops.reindex_codegraph_resume_managed_path import (
            RootOwnedRegularFileSnapshot,
        )

        self.module = module
        self.events: list[object] = []
        self.legacy_root = tmp_path / "etc" / "systemd" / "system"
        self.canonical_root = tmp_path / "usr" / "local" / "lib" / "systemd" / "system"
        self.runtime_mask_target: str | None = None
        self.reloaded = False
        self.files = {
            self.legacy_root / "codev-mcp-codegraph.service": RootOwnedRegularFileSnapshot(
                b"[Service]\nExecStart=/old/codegraph\n", 0o644, 0, 0
            ),
            self.legacy_root / "codev-reindex.service": RootOwnedRegularFileSnapshot(
                b"[Service]\nExecStart=/old/reindex\n", 0o644, 0, 0
            ),
        }
        self.runtime = {
            "codev-mcp-codegraph.service": _运行态(
                module,
                fragment=self.legacy_root / "codev-mcp-codegraph.service",
                active="active",
                invocation="codegraph-invocation",
            ),
            "codev-reindex.service": _运行态(
                module,
                fragment=self.legacy_root / "codev-reindex.service",
                active="failed",
                invocation="reindex-invocation",
            ),
        }
        self.enable_link_target = str(self.legacy_root / "codev-mcp-codegraph.service")

    def 构造(self):
        return self.module.UnitLayoutMigrationPorts(
            transition_lock=lambda: nullcontext(),
            gate_active=lambda: False,
            read_snapshot=self.读取原像,
            create_if_absent=self.条件创建,
            move_if_snapshot=self.按原像移动,
            read_runtime=self.读取运行态,
            systemctl=self.执行systemctl,
            read_enable_link=self.读取启用链接,
            read_runtime_mask_target=lambda: self.runtime_mask_target,
            expected_codegraph_python=lambda: "/new/python",
        )

    def 读取原像(self, path: Path):
        return self.files.get(path)

    def 条件创建(self, path: Path, snapshot) -> None:
        self.events.append(("create", path, snapshot.content))
        if path in self.files:
            raise RuntimeError("目标已存在")
        self.files[path] = snapshot

    def 按原像移动(self, source: Path, destination: Path, expected) -> None:
        self.events.append(("move", source, destination, expected.content if expected else None))
        if self.files.get(source) != expected:
            raise RuntimeError("原像已变化")
        if destination in self.files:
            raise RuntimeError("隔离目标已存在")
        self.files[destination] = self.files.pop(source)

    def 读取运行态(self, name: str):
        return self.runtime[name]

    def 执行systemctl(self, command: tuple[str, ...]) -> None:
        self.events.append(command)
        if command == ("systemctl", "unmask", "--runtime", "codev-mcp-codegraph.service"):
            self.runtime_mask_target = None
            return
        if command == ("systemctl", "daemon-reload"):
            self.reloaded = True
            for name, runtime in tuple(self.runtime.items()):
                fragment = self.legacy_root / name
                if (
                    name == "codev-mcp-codegraph.service"
                    and fragment not in self.files
                    and self.canonical_root / name in self.files
                ):
                    fragment = self.canonical_root / name
                self.runtime[name] = self.module.SystemdUnitRuntime(
                    fragment_path=str(fragment),
                    unit_file_state=runtime.unit_file_state,
                    active_state=runtime.active_state,
                    sub_state=runtime.sub_state,
                    result=runtime.result,
                    exec_main_code=runtime.exec_main_code,
                    exec_main_status=runtime.exec_main_status,
                    invocation_id=runtime.invocation_id,
                )
            return
        if command == ("systemctl", "reenable", "codev-mcp-codegraph.service"):
            canonical = self.canonical_root / "codev-mcp-codegraph.service"
            legacy = self.legacy_root / "codev-mcp-codegraph.service"
            self.enable_link_target = str(canonical if canonical in self.files else legacy)

    def 读取启用链接(self, name: str) -> str:
        if not self.reloaded:
            raise RuntimeError("未完成重载")
        assert name == "codev-mcp-codegraph.service"
        return self.enable_link_target


def _绑定路径(monkeypatch: pytest.MonkeyPatch, module, ports: _模拟端口) -> None:
    from codev_platform.ops import systemd_unit_layout_transaction as transaction

    monkeypatch.setattr(transaction, "_LEGACY_UNIT_DIRECTORY", ports.legacy_root)
    monkeypatch.setattr(transaction, "_CANONICAL_UNIT_DIRECTORY", ports.canonical_root)


def test_迁移仅将CodeGraph主unit切到canonical目录且不启停服务(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_unit_layout_migration as module

    ports = _模拟端口(module, tmp_path)
    _绑定路径(monkeypatch, module, ports)
    install_input = _安装输入(module, tmp_path)
    before = dict(ports.runtime)

    module.migrate_verified_input(
        install_input,
        ports=ports.构造(),
        platform_name="linux",
        effective_user_id=lambda: 0,
    )

    assert set(ports.files) == {
        ports.canonical_root / "codev-mcp-codegraph.service",
        ports.legacy_root / "codev-reindex.service",
        ports.legacy_root / ".codev-mcp-codegraph.service.codev-layout-backup",
    }
    assert ports.events.count(("systemctl", "daemon-reload")) == 1
    assert (
        "systemctl",
        "reenable",
        "codev-mcp-codegraph.service",
    ) in ports.events
    assert ("systemctl", "reenable", "codev-reindex.service") not in ports.events
    forbidden = {"start", "stop", "restart", "reset-failed"}
    assert not any(
        isinstance(event, tuple) and len(event) > 1 and event[1] in forbidden
        for event in ports.events
    )
    for name, original in before.items():
        current = ports.runtime[name]
        expected_fragment = (
            ports.canonical_root / name
            if name == "codev-mcp-codegraph.service"
            else ports.legacy_root / name
        )
        assert current.fragment_path == str(expected_fragment)
        assert current.without_fragment() == original.without_fragment()


def test_迁移遇到后置失败会恢复双路径原像并拒绝掩盖异常(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_unit_layout_migration as module

    ports = _模拟端口(module, tmp_path)
    _绑定路径(monkeypatch, module, ports)
    original_systemctl = ports.执行systemctl
    failed_once = False

    def 失败的systemctl(command: tuple[str, ...]) -> None:
        nonlocal failed_once
        if command == ("systemctl", "reenable", "codev-mcp-codegraph.service") and not failed_once:
            failed_once = True
            raise RuntimeError("重接链接失败")
        original_systemctl(command)

    migration_ports = ports.构造()
    object.__setattr__(migration_ports, "systemctl", 失败的systemctl)

    with pytest.raises(module.SystemdUnitLayoutMigrationError, match="已回滚"):
        module.migrate_verified_input(
            _安装输入(module, tmp_path),
            ports=migration_ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert ports.legacy_root / "codev-mcp-codegraph.service" in ports.files
    assert ports.legacy_root / "codev-reindex.service" in ports.files
    assert ports.canonical_root / "codev-mcp-codegraph.service" not in ports.files
    assert (
        ports.canonical_root / ".codev-mcp-codegraph.service.codev-layout-rollback" in ports.files
    )
    assert ports.events.count(("systemctl", "daemon-reload")) >= 2


def test_迁移后置失败会用新canonical的真实inode条件回滚(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """新建文件的占位原像不能用于需要 inode 身份的条件回滚。"""
    from codev_platform import mcp_systemd_unit_layout_migration as module
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        RootOwnedRegularFileSnapshot,
    )

    ports = _模拟端口(module, tmp_path)
    _绑定路径(monkeypatch, module, ports)
    original_create = ports.条件创建
    original_systemctl = ports.执行systemctl
    failed_once = False

    def 创建带真实身份(path: Path, snapshot) -> None:
        original_create(path, snapshot)
        ports.files[path] = RootOwnedRegularFileSnapshot(
            snapshot.content,
            snapshot.mode,
            snapshot.uid,
            snapshot.gid,
            device=17,
            inode=23,
        )

    def 后置失败(command: tuple[str, ...]) -> None:
        nonlocal failed_once
        if command == ("systemctl", "reenable", "codev-mcp-codegraph.service") and not failed_once:
            failed_once = True
            raise RuntimeError("重接链接失败")
        original_systemctl(command)

    migration_ports = ports.构造()
    object.__setattr__(migration_ports, "create_if_absent", 创建带真实身份)
    object.__setattr__(migration_ports, "systemctl", 后置失败)

    with pytest.raises(module.SystemdUnitLayoutMigrationError, match="已回滚"):
        module.migrate_verified_input(
            _安装输入(module, tmp_path),
            ports=migration_ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    canonical = ports.canonical_root / "codev-mcp-codegraph.service"
    rollback = canonical.with_name(f".{canonical.name}.codev-layout-rollback")
    assert canonical not in ports.files
    assert rollback in ports.files
    assert ports.files[rollback].inode == 23
    assert ports.legacy_root / "codev-mcp-codegraph.service" in ports.files


def test_条件创建报错后canonical身份无法证明时失败关闭(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """创建返回异常后，即使同内容也不能把可能被替换的 canonical 当作本次叶子。"""
    from codev_platform import mcp_systemd_unit_layout_migration as module
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        RootOwnedRegularFileSnapshot,
    )

    ports = _模拟端口(module, tmp_path)
    _绑定路径(monkeypatch, module, ports)
    original_create = ports.条件创建

    def 落盘后失败(path: Path, snapshot) -> None:
        original_create(path, snapshot)
        created = ports.files[path]
        ports.files[path] = RootOwnedRegularFileSnapshot(
            created.content, created.mode, created.uid, created.gid, device=7, inode=11
        )
        raise RuntimeError("目录同步失败")

    migration_ports = ports.构造()
    object.__setattr__(migration_ports, "create_if_absent", 落盘后失败)

    with pytest.raises(module.SystemdUnitLayoutMigrationError, match="安全状态未证明"):
        module.migrate_verified_input(
            _安装输入(module, tmp_path),
            ports=migration_ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    canonical = ports.canonical_root / "codev-mcp-codegraph.service"
    rollback = canonical.with_name(f".{canonical.name}.codev-layout-rollback")
    assert canonical in ports.files and rollback not in ports.files
    assert ports.files[canonical].inode == 11
    assert ports.legacy_root / "codev-mcp-codegraph.service" in ports.files


def test_legacy移动在叶子已改名后报错时仍恢复原路径(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """renameat2 已完成而 fsync 报错时，不得遗漏 legacy backup 的回移。"""
    from codev_platform import mcp_systemd_unit_layout_migration as module

    ports = _模拟端口(module, tmp_path)
    _绑定路径(monkeypatch, module, ports)
    original_move = ports.按原像移动
    legacy = ports.legacy_root / "codev-mcp-codegraph.service"
    failed_once = False

    def 改名后失败(source: Path, destination: Path, expected) -> None:
        nonlocal failed_once
        original_move(source, destination, expected)
        if source == legacy and not failed_once:
            failed_once = True
            raise RuntimeError("目录同步失败")

    migration_ports = ports.构造()
    object.__setattr__(migration_ports, "move_if_snapshot", 改名后失败)

    with pytest.raises(module.SystemdUnitLayoutMigrationError, match="已回滚"):
        module.migrate_verified_input(
            _安装输入(module, tmp_path),
            ports=migration_ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    canonical = ports.canonical_root / "codev-mcp-codegraph.service"
    rollback = canonical.with_name(f".{canonical.name}.codev-layout-rollback")
    assert legacy in ports.files
    assert rollback in ports.files
    assert ports.legacy_root / ".codev-mcp-codegraph.service.codev-layout-backup" not in ports.files


def test_legacy_backup被同内容异inode替换时失败关闭(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """补偿只能回移原叶子，不能把外部替换的 backup 覆盖回 legacy 路径。"""
    from codev_platform import mcp_systemd_unit_layout_migration as module
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        RootOwnedRegularFileSnapshot,
    )

    ports = _模拟端口(module, tmp_path)
    _绑定路径(monkeypatch, module, ports)
    legacy = ports.legacy_root / "codev-mcp-codegraph.service"
    original = ports.files[legacy]
    ports.files[legacy] = RootOwnedRegularFileSnapshot(
        original.content, original.mode, original.uid, original.gid, device=7, inode=11
    )
    original_move = ports.按原像移动

    def 改名后被替换(source: Path, destination: Path, expected) -> None:
        original_move(source, destination, expected)
        if source == legacy:
            moved = ports.files[destination]
            ports.files[destination] = RootOwnedRegularFileSnapshot(
                moved.content, moved.mode, moved.uid, moved.gid, device=7, inode=12
            )
            raise RuntimeError("隔离文件被替换")

    migration_ports = ports.构造()
    object.__setattr__(migration_ports, "move_if_snapshot", 改名后被替换)

    with pytest.raises(module.SystemdUnitLayoutMigrationError, match="安全状态未证明"):
        module.migrate_verified_input(
            _安装输入(module, tmp_path),
            ports=migration_ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert legacy not in ports.files


def test_迁移仅受理包含固定CodeGraph_unit的输入(tmp_path: Path) -> None:
    from codev_platform import mcp_systemd_unit_layout_migration as module
    from codev_platform.mcp_systemd_install_contract import (
        SystemdInstallManifest,
        SystemdUnitActivationMode,
        SystemdUnitInstallSpec,
        SystemdUnitPayload,
    )
    from codev_platform.mcp_systemd_install_input import VerifiedInstallInput

    content = b"[Service]\n"
    spec = SystemdUnitInstallSpec(
        source=(tmp_path / "codev-reindex.service").resolve(),
        content_digest=hashlib.sha256(content).hexdigest(),
        enable=True,
        restart=True,
        activation_mode=SystemdUnitActivationMode.REINDEX_STATE_MACHINE,
    )
    install_input = VerifiedInstallInput(
        manifest=SystemdInstallManifest((spec,), runtime_revision="1" * 40),
        payloads=(SystemdUnitPayload(spec=spec, content=content),),
    )

    with pytest.raises(module.SystemdUnitLayoutMigrationError, match="固定 CodeGraph"):
        module.select_fixed_payloads(install_input)


def test_遗留的无效runtime_mask会在迁移前受控清除(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_unit_layout_migration as module

    ports = _模拟端口(module, tmp_path)
    ports.runtime_mask_target = "/dev/null"
    _绑定路径(monkeypatch, module, ports)

    module.migrate_verified_input(
        _安装输入(module, tmp_path),
        ports=ports.构造(),
        platform_name="linux",
        effective_user_id=lambda: 0,
    )

    unmask = ("systemctl", "unmask", "--runtime", "codev-mcp-codegraph.service")
    assert unmask in ports.events
    assert ports.events.index(unmask) < next(
        index
        for index, event in enumerate(ports.events)
        if isinstance(event, tuple) and event[0] == "create"
    )


def test_双主文件但canonical内容未知时失败关闭(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_unit_layout_migration as module
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        RootOwnedRegularFileSnapshot,
    )

    ports = _模拟端口(module, tmp_path)
    _绑定路径(monkeypatch, module, ports)
    ports.files[ports.canonical_root / "codev-mcp-codegraph.service"] = (
        RootOwnedRegularFileSnapshot(b"[Service]\nExecStart=/unknown\n", 0o644, 0, 0)
    )

    with pytest.raises(module.SystemdUnitLayoutMigrationError, match="双主文件"):
        module.migrate_verified_input(
            _安装输入(module, tmp_path),
            ports=ports.构造(),
            platform_name="linux",
            effective_user_id=lambda: 0,
        )


def test_中断后仅剩canonical文件时会继续完成reload与enable重接(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """写新文件、删旧文件之间被中断后，下一次受控运行不能把状态误判成不可恢复。"""
    from codev_platform import mcp_systemd_unit_layout_migration as module
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        RootOwnedRegularFileSnapshot,
    )

    ports = _模拟端口(module, tmp_path)
    _绑定路径(monkeypatch, module, ports)
    install_input = _安装输入(module, tmp_path)
    payload = module.select_fixed_payloads(install_input)
    ports.files.pop(ports.legacy_root / "codev-mcp-codegraph.service")
    ports.files[ports.canonical_root / "codev-mcp-codegraph.service"] = (
        RootOwnedRegularFileSnapshot(payload.content, 0o644, 0, 0)
    )

    module.migrate_verified_input(
        install_input,
        ports=ports.构造(),
        platform_name="linux",
        effective_user_id=lambda: 0,
    )

    assert not any(
        isinstance(event, tuple) and event[0] in {"create", "move"} for event in ports.events
    )
    assert ("systemctl", "daemon-reload") in ports.events
    assert ("systemctl", "reenable", "codev-mcp-codegraph.service") in ports.events


def test_reload完成但reenable前中断时重试会收敛旧enable链接(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """canonical 已被 systemd 解析不代表 wants 链接已经安全切换。"""
    from codev_platform import mcp_systemd_unit_layout_migration as module
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        RootOwnedRegularFileSnapshot,
    )

    ports = _模拟端口(module, tmp_path)
    _绑定路径(monkeypatch, module, ports)
    install_input = _安装输入(module, tmp_path)
    payload = module.select_fixed_payloads(install_input)
    legacy = ports.legacy_root / "codev-mcp-codegraph.service"
    canonical = ports.canonical_root / "codev-mcp-codegraph.service"
    ports.files.pop(legacy)
    ports.files[canonical] = RootOwnedRegularFileSnapshot(payload.content, 0o644, 0, 0)
    ports.runtime[payload.spec.unit_name] = _运行态(
        module,
        fragment=canonical,
        active="active",
        invocation="codegraph-invocation",
    )
    ports.reloaded = True
    ports.enable_link_target = str(legacy)

    module.migrate_verified_input(
        install_input,
        ports=ports.构造(),
        platform_name="linux",
        effective_user_id=lambda: 0,
    )

    assert ("systemctl", "reenable", payload.spec.unit_name) in ports.events
    assert ports.enable_link_target == str(canonical)


def test_canonical_only内容与当前manifest不同时拒绝且不执行systemctl(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """中断恢复仍必须绑定本次 release 的 manifest，不得默许旧版本主 unit。"""
    from codev_platform import mcp_systemd_unit_layout_migration as module
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        RootOwnedRegularFileSnapshot,
    )

    ports = _模拟端口(module, tmp_path)
    _绑定路径(monkeypatch, module, ports)
    install_input = _安装输入(module, tmp_path)
    payload = module.select_fixed_payloads(install_input)
    legacy = ports.legacy_root / payload.spec.unit_name
    canonical = ports.canonical_root / payload.spec.unit_name
    ports.files.pop(legacy)
    ports.files[canonical] = RootOwnedRegularFileSnapshot(
        b"[Service]\nExecStart=/other-release/codegraph\n", 0o644, 0, 0
    )
    ports.runtime[payload.spec.unit_name] = _运行态(
        module,
        fragment=canonical,
        active="active",
        invocation="codegraph-invocation",
    )

    with pytest.raises(module.SystemdUnitLayoutMigrationError, match="canonical"):
        module.migrate_verified_input(
            install_input,
            ports=ports.构造(),
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert ports.events == []


def test_迁移端口缺少目标解释器证明时在副作用前拒绝(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """解释器证明是迁移契约的一部分，不能延迟到写文件后才发现缺失。"""
    from codev_platform import mcp_systemd_unit_layout_migration as module

    ports = _模拟端口(module, tmp_path)
    _绑定路径(monkeypatch, module, ports)
    invalid_ports = replace(ports.构造(), expected_codegraph_python=None)

    with pytest.raises(module.SystemdUnitLayoutMigrationError, match="迁移适配器不可用"):
        module.migrate_verified_input(
            _安装输入(module, tmp_path),
            ports=invalid_ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert ports.events == []
