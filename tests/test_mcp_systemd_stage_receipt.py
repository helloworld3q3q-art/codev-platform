"""systemd maintenance-stage 发布回执与目标版本绑定测试。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from codev_platform.core.runtime_interpreter import ReleaseInterpreterIdentity
from tests.mcp_systemd_install_transaction_support import _维护清单, _端口


_TARGET_A = "a" * 40
_TARGET_B = "b" * 40
_RELEASE_A = ReleaseInterpreterIdentity(
    runtime_revision=_TARGET_A,
    release_id="c" * 64,
    interpreter_path="/release/venv/bin/python",
)
_RELEASE_B_REVISION = ReleaseInterpreterIdentity(
    runtime_revision=_TARGET_B,
    release_id=_RELEASE_A.release_id,
    interpreter_path=_RELEASE_A.interpreter_path,
)
_RELEASE_B_ID = ReleaseInterpreterIdentity(
    runtime_revision=_TARGET_A,
    release_id="d" * 64,
    interpreter_path=_RELEASE_A.interpreter_path,
)
_RELEASE_B_INTERPRETER = ReleaseInterpreterIdentity(
    runtime_revision=_TARGET_A,
    release_id=_RELEASE_A.release_id,
    interpreter_path="/other/venv/bin/python",
)
_RESUME_DROPIN_CONTENT = (
    b"[Service]\nEnvironmentFile=\n"
    b"EnvironmentFile=/etc/codev-platform/reindex-codegraph-resume.env\n"
)


def _protected_content(
    name: str,
    *,
    interpreter: str = _RELEASE_A.interpreter_path,
    reindex_module: str = "codev_platform.cli",
    include_codegraph_condition: bool = True,
    codegraph_port: int | None = 18091,
) -> bytes:
    if name == "codev-reindex.service":
        return (
            "[Service]\n"
            f"ExecStart={interpreter} -I -m {reindex_module} reindex-queue worker "
            "--require-execution-mode isolated\n"
        ).encode()
    condition = (
        "ConditionPathExists=!/var/lib/codev-platform/codegraph-maintenance.gate\n"
        if include_codegraph_condition
        else ""
    )
    command = (
        f"ExecStart={interpreter} -I -m codev_platform.codegraph.server --http"
        if codegraph_port is None
        else (
            f"ExecStart={interpreter} -I -m codev_platform.codegraph.server "
            f"--http --port {codegraph_port}"
        )
    )
    return (f"[Unit]\n{condition}[Service]\n{command}\n").encode()


def _protected_manifest(
    tmp_path: Path,
    *,
    revision: str = _TARGET_A,
    interpreter: str = _RELEASE_A.interpreter_path,
    reindex_module: str = "codev_platform.cli",
    include_codegraph_condition: bool = True,
    codegraph_port: int | None = 18091,
):
    from codev_platform import mcp_systemd_install_transaction as module

    reindex_content = _protected_content(
        "codev-reindex.service",
        interpreter=interpreter,
        reindex_module=reindex_module,
    )
    codegraph_content = _protected_content(
        "codev-mcp-codegraph.service",
        interpreter=interpreter,
        include_codegraph_condition=include_codegraph_condition,
        codegraph_port=codegraph_port,
    )
    return module.SystemdInstallManifest(
        units=(
            module.SystemdUnitInstallSpec(
                source=(tmp_path / "codev-reindex.service").resolve(),
                content_digest=hashlib.sha256(reindex_content).hexdigest(),
                enable=True,
                restart=True,
                activation_mode=module.SystemdUnitActivationMode.REINDEX_STATE_MACHINE,
            ),
            module.SystemdUnitInstallSpec(
                source=(tmp_path / "codev-mcp-codegraph.service").resolve(),
                content_digest=hashlib.sha256(codegraph_content).hexdigest(),
                enable=True,
                restart=True,
                activation_mode=module.SystemdUnitActivationMode.CODEGRAPH_STATE_MACHINE,
            ),
        ),
        runtime_revision=revision,
    )


def _protected_payloads(
    tmp_path: Path,
    *,
    interpreter: str = _RELEASE_A.interpreter_path,
    reindex_module: str = "codev_platform.cli",
    include_codegraph_condition: bool = True,
    codegraph_port: int | None = 18091,
):
    from codev_platform.mcp_systemd_install_contract import SystemdUnitPayload

    manifest = _protected_manifest(
        tmp_path,
        interpreter=interpreter,
        reindex_module=reindex_module,
        include_codegraph_condition=include_codegraph_condition,
        codegraph_port=codegraph_port,
    )
    return tuple(
        SystemdUnitPayload(
            spec=spec,
            content=_protected_content(
                spec.unit_name,
                interpreter=interpreter,
                reindex_module=reindex_module,
                include_codegraph_condition=include_codegraph_condition,
                codegraph_port=codegraph_port,
            ),
        )
        for spec in manifest.units
    )


def test_manifest_v4显式序列化唯一运行时版本(tmp_path: Path) -> None:
    from codev_platform.mcp_systemd_install_contract import SystemdInstallManifest

    manifest = _protected_manifest(tmp_path)

    mapping = manifest.to_mapping()

    assert mapping["version"] == 4
    assert mapping["runtime_revision"] == _TARGET_A
    assert SystemdInstallManifest.from_mapping(mapping) == manifest


@pytest.mark.parametrize("revision", ["A" * 40, "a" * 39, "g" * 40, "a" * 41])
def test_manifest拒绝非完整小写运行时摘要(tmp_path: Path, revision: str) -> None:
    from codev_platform.mcp_systemd_install_contract import SystemdInstallTransactionError

    with pytest.raises(SystemdInstallTransactionError, match="运行时版本"):
        _protected_manifest(tmp_path, revision=revision)


def test_stage回执绑定完整发布身份和两个受保护unit摘要(tmp_path: Path) -> None:
    from codev_platform.mcp_systemd_stage_receipt import build_stage_receipt_content

    manifest = _protected_manifest(tmp_path)
    payload = json.loads(build_stage_receipt_content(manifest, _RELEASE_A))

    assert payload == {
        "schema": 3,
        "runtime": {
            "interpreter_path": _RELEASE_A.interpreter_path,
            "release_id": _RELEASE_A.release_id,
            "runtime_revision": _TARGET_A,
        },
        "protected_units": {unit.unit_name: unit.content_digest for unit in manifest.units},
        "enabled_units": [
            "codev-reindex.service",
            "codev-mcp-codegraph.service",
            "codev-webhook.service",
        ],
    }


def test_stage回执拒绝manifest与当前发布版本不一致(tmp_path: Path) -> None:
    from codev_platform.mcp_systemd_stage_receipt import (
        StageReceiptError,
        build_stage_receipt_content,
    )

    with pytest.raises(StageReceiptError, match="实际发布运行时"):
        build_stage_receipt_content(_protected_manifest(tmp_path), _RELEASE_B_REVISION)


def test_stage回执读取器按目标提交返回受保护unit期望摘要(tmp_path: Path) -> None:
    from codev_platform.mcp_systemd_install_contract import SystemdStageReceiptFileSnapshot
    from codev_platform.mcp_systemd_stage_receipt import (
        build_stage_receipt_content,
        read_stage_receipt_expected_digests,
    )

    manifest = _protected_manifest(tmp_path)
    snapshot = SystemdStageReceiptFileSnapshot(
        content=build_stage_receipt_content(manifest, _RELEASE_A),
        mode=0o600,
        uid=0,
        gid=0,
    )

    assert read_stage_receipt_expected_digests(
        _RELEASE_A,
        snapshot_reader=lambda: snapshot,
    ) == {unit.unit_name: unit.content_digest for unit in manifest.units}


@pytest.mark.parametrize(
    "other_release",
    [_RELEASE_B_REVISION, _RELEASE_B_ID, _RELEASE_B_INTERPRETER],
)
def test_stage回执读取器拒绝任一发布身份字段串线(
    tmp_path: Path,
    other_release: ReleaseInterpreterIdentity,
) -> None:
    from codev_platform.mcp_systemd_install_contract import SystemdStageReceiptFileSnapshot
    from codev_platform.mcp_systemd_stage_receipt import (
        StageReceiptError,
        build_stage_receipt_content,
        read_stage_receipt_expected_digests,
    )

    snapshot = SystemdStageReceiptFileSnapshot(
        content=build_stage_receipt_content(
            _protected_manifest(tmp_path),
            _RELEASE_A,
        ),
        mode=0o600,
        uid=0,
        gid=0,
    )

    with pytest.raises(StageReceiptError, match="发布运行身份"):
        read_stage_receipt_expected_digests(
            other_release,
            snapshot_reader=lambda: snapshot,
        )


@pytest.mark.parametrize("mode", [0o640, 0o644])
def test_stage回执读取器拒绝非精确权限(tmp_path: Path, mode: int) -> None:
    from codev_platform.mcp_systemd_install_contract import SystemdStageReceiptFileSnapshot
    from codev_platform.mcp_systemd_stage_receipt import (
        StageReceiptError,
        build_stage_receipt_content,
        read_stage_receipt_expected_digests,
    )

    snapshot = SystemdStageReceiptFileSnapshot(
        content=build_stage_receipt_content(
            _protected_manifest(tmp_path),
            _RELEASE_A,
        ),
        mode=mode,
        uid=0,
        gid=0,
    )

    with pytest.raises(StageReceiptError, match="不受信任"):
        read_stage_receipt_expected_digests(
            _RELEASE_A,
            snapshot_reader=lambda: snapshot,
        )


def test_stage回执复证拒绝摘要错配(tmp_path: Path) -> None:
    from codev_platform.mcp_systemd_install_contract import SystemdStageReceiptFileSnapshot
    from codev_platform.mcp_systemd_stage_receipt import (
        StageReceiptError,
        build_stage_receipt_content,
        verify_stage_receipt_content,
    )

    expected = build_stage_receipt_content(_protected_manifest(tmp_path), _RELEASE_A)
    tampered_payload = json.loads(expected)
    tampered_payload["runtime"]["release_id"] = "f" * 64
    tampered = (
        json.dumps(
            tampered_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    snapshot = SystemdStageReceiptFileSnapshot(
        content=tampered,
        mode=0o600,
        uid=0,
        gid=0,
    )

    with pytest.raises(StageReceiptError, match="复证失败"):
        verify_stage_receipt_content(expected, snapshot_reader=lambda: snapshot)


@pytest.mark.parametrize("require_effective", [False, True])
def test_stage载荷证明按阶段校验canonical并可追加effective证明(
    tmp_path: Path,
    require_effective: bool,
) -> None:
    from codev_platform.mcp_systemd_install_contract import (
        SystemdStageReceiptFileSnapshot,
        SystemdUnitFileSnapshot,
    )
    from codev_platform.mcp_systemd_stage_receipt import (
        build_stage_receipt_content,
        prove_staged_systemd_payload,
    )

    manifest = _protected_manifest(tmp_path)
    receipt = SystemdStageReceiptFileSnapshot(
        build_stage_receipt_content(manifest, _RELEASE_A), 0o600, 0, 0
    )
    effective_calls: list[tuple[object, ...]] = []
    enabled_calls: list[str] = []

    prove_staged_systemd_payload(
        _RELEASE_A,
        require_effective=require_effective,
        resume_dropin_content=(_RESUME_DROPIN_CONTENT if require_effective else None),
        snapshot_reader=lambda: receipt,
        unit_snapshot_reader=lambda name: SystemdUnitFileSnapshot(
            _protected_content(name), 0o644, 0, 0
        ),
        unit_source_resolver=lambda name: (tmp_path / name).resolve(),
        effective_payload_verifier=lambda payloads: effective_calls.append(payloads),
        unit_enabled_probe=lambda name: enabled_calls.append(name) or True,
    )

    assert enabled_calls == [
        "codev-reindex.service",
        "codev-mcp-codegraph.service",
        "codev-webhook.service",
    ]
    assert bool(effective_calls) is require_effective
    if effective_calls:
        assert {payload.spec.unit_name for payload in effective_calls[0]} == {
            "codev-reindex.service",
            "codev-mcp-codegraph.service",
        }


def test_stage有效证明把bridge原像与覆盖启动命令交给默认验证器(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as installer
    from codev_platform.mcp_systemd_install_contract import (
        SystemdStageReceiptFileSnapshot,
        SystemdUnitFileSnapshot,
    )
    from codev_platform.mcp_systemd_stage_receipt import (
        build_stage_receipt_content,
        prove_staged_systemd_payload,
    )

    manifest = _protected_manifest(tmp_path)
    receipt = SystemdStageReceiptFileSnapshot(
        build_stage_receipt_content(manifest, _RELEASE_A), 0o600, 0, 0
    )
    bridge_content = b"[Service]\nExecStart=bridge\n"
    bridge_exec_start = (
        "/release/venv/bin/python",
        "-I",
        "-B",
        "-c",
        "bridge",
    )
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        installer,
        "default_staged_effective_unit_payload_verifier",
        lambda payloads, **kwargs: calls.append((payloads, kwargs)),
    )

    prove_staged_systemd_payload(
        _RELEASE_A,
        require_effective=True,
        resume_dropin_content=_RESUME_DROPIN_CONTENT,
        codegraph_startup_bridge_dropin_content=bridge_content,
        codegraph_effective_exec_start=bridge_exec_start,
        snapshot_reader=lambda: receipt,
        unit_snapshot_reader=lambda name: SystemdUnitFileSnapshot(
            _protected_content(name), 0o644, 0, 0
        ),
        unit_source_resolver=lambda name: (tmp_path / name).resolve(),
        unit_enabled_probe=lambda _name: True,
    )

    assert len(calls) == 1
    payloads, kwargs = calls[0]
    assert {payload.spec.unit_name for payload in payloads} == {
        "codev-reindex.service",
        "codev-mcp-codegraph.service",
    }
    assert kwargs == {
        "resume_dropin_content": _RESUME_DROPIN_CONTENT,
        "allow_reindex_local_dropins": False,
        "codegraph_startup_bridge_dropin_content": bridge_content,
        "codegraph_effective_exec_start": bridge_exec_start,
    }


def test_stage非有效阶段拒绝携带bridge契约(tmp_path: Path) -> None:
    from codev_platform.mcp_systemd_install_contract import (
        SystemdStageReceiptFileSnapshot,
        SystemdUnitFileSnapshot,
    )
    from codev_platform.mcp_systemd_stage_receipt import (
        StageReceiptError,
        build_stage_receipt_content,
        prove_staged_systemd_payload,
    )

    manifest = _protected_manifest(tmp_path)
    receipt = SystemdStageReceiptFileSnapshot(
        build_stage_receipt_content(manifest, _RELEASE_A), 0o600, 0, 0
    )

    with pytest.raises(StageReceiptError, match="非有效载荷阶段"):
        prove_staged_systemd_payload(
            _RELEASE_A,
            require_effective=False,
            codegraph_startup_bridge_dropin_content=b"bridge\n",
            codegraph_effective_exec_start=("/release/venv/bin/python",),
            snapshot_reader=lambda: receipt,
            unit_snapshot_reader=lambda name: SystemdUnitFileSnapshot(
                _protected_content(name), 0o644, 0, 0
            ),
            unit_source_resolver=lambda name: (tmp_path / name).resolve(),
            unit_enabled_probe=lambda _name: True,
        )


def test_stage载荷证明拒绝canonical摘要错配(tmp_path: Path) -> None:
    from codev_platform.mcp_systemd_install_contract import (
        SystemdStageReceiptFileSnapshot,
        SystemdUnitFileSnapshot,
    )
    from codev_platform.mcp_systemd_stage_receipt import (
        StageReceiptError,
        build_stage_receipt_content,
        prove_staged_systemd_payload,
    )

    manifest = _protected_manifest(tmp_path)
    receipt = SystemdStageReceiptFileSnapshot(
        build_stage_receipt_content(manifest, _RELEASE_A), 0o600, 0, 0
    )

    with pytest.raises(StageReceiptError, match="canonical"):
        prove_staged_systemd_payload(
            _RELEASE_A,
            require_effective=False,
            snapshot_reader=lambda: receipt,
            unit_snapshot_reader=lambda _name: SystemdUnitFileSnapshot(b"tampered\n", 0o644, 0, 0),
            unit_source_resolver=lambda name: (tmp_path / name).resolve(),
            effective_payload_verifier=lambda _payloads: None,
            unit_enabled_probe=lambda _name: True,
        )


def test_stage载荷证明在解除mask前拒绝CodeGraph缺少显式端口(tmp_path: Path) -> None:
    from codev_platform.mcp_systemd_install_contract import (
        SystemdStageReceiptFileSnapshot,
        SystemdUnitFileSnapshot,
    )
    from codev_platform.mcp_systemd_stage_receipt import (
        StageReceiptError,
        build_stage_receipt_content,
        prove_staged_systemd_payload,
    )

    manifest = _protected_manifest(tmp_path, codegraph_port=None)
    receipt = SystemdStageReceiptFileSnapshot(
        build_stage_receipt_content(manifest, _RELEASE_A), 0o600, 0, 0
    )

    with pytest.raises(StageReceiptError, match="启动入口"):
        prove_staged_systemd_payload(
            _RELEASE_A,
            require_effective=False,
            snapshot_reader=lambda: receipt,
            unit_snapshot_reader=lambda name: SystemdUnitFileSnapshot(
                _protected_content(name, codegraph_port=None), 0o644, 0, 0
            ),
            unit_source_resolver=lambda name: (tmp_path / name).resolve(),
            unit_enabled_probe=lambda _name: True,
        )


def test_stage载荷证明拒绝任一延迟单元未启用(tmp_path: Path) -> None:
    from codev_platform.mcp_systemd_install_contract import (
        SystemdStageReceiptFileSnapshot,
        SystemdUnitFileSnapshot,
    )
    from codev_platform.mcp_systemd_stage_receipt import (
        StageReceiptError,
        build_stage_receipt_content,
        prove_staged_systemd_payload,
    )

    manifest = _protected_manifest(tmp_path)
    receipt = SystemdStageReceiptFileSnapshot(
        build_stage_receipt_content(manifest, _RELEASE_A), 0o600, 0, 0
    )

    with pytest.raises(StageReceiptError, match="启用状态"):
        prove_staged_systemd_payload(
            _RELEASE_A,
            require_effective=False,
            snapshot_reader=lambda: receipt,
            unit_snapshot_reader=lambda name: SystemdUnitFileSnapshot(
                _protected_content(name), 0o644, 0, 0
            ),
            unit_source_resolver=lambda name: (tmp_path / name).resolve(),
            effective_payload_verifier=lambda _payloads: None,
            unit_enabled_probe=lambda name: name != "codev-mcp-codegraph.service",
        )


@pytest.mark.parametrize(
    ("interpreter", "reindex_module", "message"),
    [
        ("/old/venv/bin/python", "codev_platform.cli", "当前发布解释器"),
        (
            _RELEASE_A.interpreter_path,
            "codev_platform.legacy_cli",
            "reindex unit 启动入口",
        ),
    ],
)
def test_stage受保护载荷拒绝旧解释器或错误入口(
    tmp_path: Path,
    interpreter: str,
    reindex_module: str,
    message: str,
) -> None:
    from codev_platform.mcp_systemd_stage_receipt import (
        StageReceiptError,
        verify_stage_payload_runtime_identity,
    )

    with pytest.raises(StageReceiptError, match=message):
        verify_stage_payload_runtime_identity(
            _protected_payloads(
                tmp_path,
                interpreter=interpreter,
                reindex_module=reindex_module,
            ),
            _RELEASE_A,
        )


def test_stage受保护载荷接受当前发布解释器与固定入口(tmp_path: Path) -> None:
    from codev_platform.mcp_systemd_stage_receipt import (
        verify_stage_payload_runtime_identity,
    )

    verify_stage_payload_runtime_identity(_protected_payloads(tmp_path), _RELEASE_A)


def test_stage受保护载荷要求CodeGraph端口绑定恢复健康端口(tmp_path: Path) -> None:
    from codev_platform.mcp_systemd_stage_receipt import (
        StageReceiptError,
        verify_stage_payload_runtime_identity,
    )

    payloads = _protected_payloads(tmp_path)
    verify_stage_payload_runtime_identity(payloads, _RELEASE_A, codegraph_port=18091)

    with pytest.raises(StageReceiptError, match="恢复健康端口"):
        verify_stage_payload_runtime_identity(payloads, _RELEASE_A, codegraph_port=19091)


def test_stage受保护载荷拒绝CodeGraph缺少耐久启动条件(tmp_path: Path) -> None:
    from codev_platform.mcp_systemd_stage_receipt import (
        StageReceiptError,
        verify_stage_payload_runtime_identity,
    )

    with pytest.raises(StageReceiptError, match="耐久维护条件"):
        verify_stage_payload_runtime_identity(
            _protected_payloads(tmp_path, include_codegraph_condition=False),
            _RELEASE_A,
        )


def test_stage回执默认写入器只使用固定可信路径和0600权限(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform.mcp_systemd_stage_receipt import (
        STAGE_RECEIPT_PATH,
        build_stage_receipt_content,
        default_stage_receipt_writer,
    )
    from codev_platform.ops import reindex_codegraph_resume_managed_path as managed

    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        managed,
        "write_root_owned_regular_file_atomic",
        lambda path, content, **metadata: calls.append((path, content, metadata)),
    )
    content = build_stage_receipt_content(_protected_manifest(tmp_path), _RELEASE_A)

    default_stage_receipt_writer(content)

    assert calls == [(STAGE_RECEIPT_PATH, content, {"mode": 0o600, "uid": 0, "gid": 0})]


def test_stage回执默认读取器拒绝不可信固定路径(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.mcp_systemd_stage_receipt import (
        StageReceiptError,
        default_stage_receipt_snapshot_reader,
    )
    from codev_platform.ops import reindex_codegraph_resume_managed_path as managed

    def reject_path(*_args, **_kwargs):
        raise managed.TrustedManagedPathError("符号链接")

    monkeypatch.setattr(
        managed,
        "read_optional_root_owned_regular_file_snapshot",
        reject_path,
    )

    with pytest.raises(StageReceiptError, match="路径不受信任"):
        default_stage_receipt_snapshot_reader()


def test_maintenance_stage在全部载荷证明后写入并复证回执(tmp_path: Path) -> None:
    from codev_platform import mcp_systemd_install_transaction as module

    manifest = replace(_维护清单(module, tmp_path), runtime_revision=_TARGET_A)
    events: list[object] = []
    ports, _files, _states, _initial_files, _initial_states = _端口(
        module,
        manifest,
        events,
    )
    receipt: module.SystemdStageReceiptFileSnapshot | None = None

    def snapshot_receipt():
        events.append("读取回执原像")
        return receipt

    def write_receipt(content: bytes) -> None:
        nonlocal receipt
        events.append("写入回执")
        receipt = module.SystemdStageReceiptFileSnapshot(content, 0o600, 0, 0)

    def verify_receipt(content: bytes) -> None:
        events.append("复证回执")
        assert receipt == module.SystemdStageReceiptFileSnapshot(content, 0o600, 0, 0)

    ports = replace(
        ports,
        snapshot_stage_receipt=snapshot_receipt,
        write_stage_receipt=write_receipt,
        verify_stage_receipt=verify_receipt,
    )

    module.install_systemd_maintenance_stage_transaction(
        manifest,
        ports=ports,
        platform_name="linux",
        effective_user_id=lambda: 0,
    )

    payload_proof = next(
        index
        for index, event in enumerate(events)
        if isinstance(event, tuple) and event[0] == "验证有效载荷"
    )
    shadow_proof = next(
        index
        for index, event in enumerate(events)
        if isinstance(event, tuple) and event[0] == "验证shadow退役"
    )
    running_proof = next(
        index
        for index, event in enumerate(events)
        if isinstance(event, tuple) and event[0] == "验证运行"
    )
    write_index = events.index("写入回执")
    assert max(payload_proof, shadow_proof, running_proof) < write_index
    assert write_index < events.index("复证回执")


def test_stage回执复证失败时恢复旧回执与全部载荷(tmp_path: Path) -> None:
    from codev_platform import mcp_systemd_install_transaction as module

    manifest = replace(_维护清单(module, tmp_path), runtime_revision=_TARGET_A)
    events: list[object] = []
    ports, files, states, initial_files, initial_states = _端口(
        module,
        manifest,
        events,
    )
    original = module.SystemdStageReceiptFileSnapshot(b"old-receipt\n", 0o600, 0, 0)
    receipt: module.SystemdStageReceiptFileSnapshot | None = original

    def snapshot_receipt():
        return receipt

    def write_receipt(content: bytes) -> None:
        nonlocal receipt
        receipt = module.SystemdStageReceiptFileSnapshot(content, 0o600, 0, 0)

    def restore_receipt(snapshot) -> None:
        nonlocal receipt
        events.append("恢复回执")
        receipt = snapshot

    ports = replace(
        ports,
        snapshot_stage_receipt=snapshot_receipt,
        write_stage_receipt=write_receipt,
        restore_stage_receipt=restore_receipt,
        verify_stage_receipt=lambda _content: (_ for _ in ()).throw(RuntimeError("摘要错配")),
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="已回滚"):
        module.install_systemd_maintenance_stage_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert receipt == original
    assert files == initial_files
    assert states == initial_states
    assert "恢复回执" in events
    first_unit_restore = next(
        index
        for index, event in enumerate(events)
        if isinstance(event, tuple) and event[0] == "恢复文件"
    )
    assert events.index("恢复回执") < first_unit_restore


def test_maintenance_stage拒绝非Git运行时版本且零修改(tmp_path: Path) -> None:
    from codev_platform import mcp_systemd_install_transaction as module

    manifest = replace(_维护清单(module, tmp_path), runtime_revision="c" * 64)
    events: list[object] = []
    ports, _files, _states, _initial_files, _initial_states = _端口(
        module,
        manifest,
        events,
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="Git"):
        module.install_systemd_maintenance_stage_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert not any(isinstance(event, tuple) and event[0] == "写入" for event in events)
