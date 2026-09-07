"""systemd stage 恢复 drop-in 的严格证明测试。"""

from __future__ import annotations

import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.mcp_systemd_effective_payload_support import (
    服务内容,
    载荷,
    属性输出,
    目标原像,
    配置codegraph_stage有效证明,
)


def test_stage有效证明要求reindex部署门禁与恢复配置共存(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module
    from codev_platform.ops.reindex_codegraph_resume_contract import REINDEX_RESUME_DROPIN
    from codev_platform.runtime_systemd_gate_contract import (
        DEPLOYMENT_GUARD_DROP_IN_CONTENT,
        deployment_guard_drop_in_path,
    )

    unit = "codev-reindex.service"
    command = "/release/bin/python -I -m codev_platform.cli reindex-queue worker"
    content = 服务内容(command)
    payload = 载荷(module, tmp_path, unit, content)
    deployment_path = deployment_guard_drop_in_path(unit)
    resume_content = b"[Service]\nEnvironmentFile=/etc/codev-platform/reindex.env\n"
    snapshots = {
        deployment_path.as_posix(): DEPLOYMENT_GUARD_DROP_IN_CONTENT,
        REINDEX_RESUME_DROPIN.as_posix(): resume_content,
    }
    monkeypatch.setattr(
        module,
        "default_unit_snapshot_reader",
        lambda _name: 目标原像(module, content),
    )
    monkeypatch.setattr(
        module,
        "_run_systemctl_command",
        lambda _command: SimpleNamespace(
            returncode=0,
            stdout=属性输出(
                "/etc/systemd/system/codev-reindex.service",
                dropins=(f"{deployment_path.as_posix()} {REINDEX_RESUME_DROPIN.as_posix()}"),
                command=command,
            ),
        ),
    )
    monkeypatch.setattr(
        module,
        "default_drop_in_snapshot_reader",
        lambda path: 目标原像(module, snapshots[path.as_posix()]),
    )

    module.default_staged_effective_unit_payload_verifier(
        (payload,),
        resume_dropin_content=resume_content,
    )


def test_stage_M1保留reindex本地运行覆盖但严格模式拒绝它们(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module
    from codev_platform.ops.reindex_codegraph_resume_contract import REINDEX_RESUME_DROPIN
    from codev_platform.runtime_systemd_gate_contract import deployment_guard_drop_in_path

    unit = "codev-reindex.service"
    command = "/release/bin/python -I -m codev_platform.cli reindex-queue worker"
    content = 服务内容(command)
    payload = 载荷(module, tmp_path, unit, content)
    deployment_path = deployment_guard_drop_in_path(unit)
    resume_content = b"[Service]\nEnvironmentFile=/etc/codev-platform/reindex.env\n"
    dropins = " ".join(
        (
            deployment_path.as_posix(),
            "/etc/systemd/system/codev-reindex.service.d/10-codev-reindex-maintenance.conf",
            "/etc/systemd/system/codev-reindex.service.d/20-codev-cpu-embedding.conf",
            REINDEX_RESUME_DROPIN.as_posix(),
            "/etc/systemd/system/codev-reindex.service.d/30-codev-start-limit.conf",
        )
    )
    monkeypatch.setattr(
        module,
        "default_unit_snapshot_reader",
        lambda _name: 目标原像(module, content),
    )
    monkeypatch.setattr(
        module,
        "_run_systemctl_command",
        lambda _command: SimpleNamespace(
            returncode=0,
            stdout=属性输出(
                "/etc/systemd/system/codev-reindex.service",
                dropins=dropins,
                command=command,
            ),
        ),
    )
    monkeypatch.setattr(
        module,
        "default_drop_in_snapshot_reader",
        lambda _path: pytest.fail("M1 本地 reindex 覆盖不得被无条件接受为受管原像"),
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="路径集合"):
        module.default_staged_effective_unit_payload_verifier(
            (payload,),
            resume_dropin_content=resume_content,
        )

    module.default_staged_effective_unit_payload_verifier(
        (payload,),
        resume_dropin_content=resume_content,
        allow_reindex_local_dropins=True,
    )


def test_stage有效证明拒绝已知恢复dropin原路径内容被追加条件重置(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module

    (
        payload,
        expected_dropin,
        deployment_path,
        deployment_content,
        resume_path,
        _guard_path,
        guard_content,
    ) = 配置codegraph_stage有效证明(
        module,
        monkeypatch,
        tmp_path,
    )
    monkeypatch.setattr(
        module,
        "default_drop_in_snapshot_reader",
        lambda path: 目标原像(
            module,
            (
                expected_dropin + b"[Unit]\nConditionPathExists=\n"
                if path.as_posix() == resume_path.as_posix()
                else (
                    deployment_content
                    if path.as_posix() == deployment_path.as_posix()
                    else guard_content
                )
            ),
        ),
        raising=False,
    )
    with pytest.raises(module.SystemdInstallTransactionError, match="drop-in"):
        module.default_staged_effective_unit_payload_verifier(
            (payload,),
            resume_dropin_content=expected_dropin,
            allow_reindex_local_dropins=True,
        )


def test_stage有效证明接受固定部署门禁恢复配置与维护门禁(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module

    (
        payload,
        expected_dropin,
        deployment_path,
        deployment_content,
        resume_path,
        guard_path,
        guard_content,
    ) = 配置codegraph_stage有效证明(module, monkeypatch, tmp_path)
    contents = {
        deployment_path.as_posix(): deployment_content,
        resume_path.as_posix(): expected_dropin,
        guard_path.as_posix(): guard_content,
    }
    reads: list[object] = []
    monkeypatch.setattr(
        module,
        "default_drop_in_snapshot_reader",
        lambda path: (
            reads.append(path)
            or 目标原像(
                module,
                contents[path.as_posix()],
            )
        ),
    )

    module.default_staged_effective_unit_payload_verifier(
        (payload,),
        resume_dropin_content=expected_dropin,
    )

    assert [path.as_posix() for path in reads] == [
        deployment_path.as_posix(),
        resume_path.as_posix(),
        guard_path.as_posix(),
    ]


def test_stage有效证明在M1接受精确bridge原像与覆盖启动命令(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module
    from codev_platform.ops.reindex_codegraph_resume_contract import (
        CODEGRAPH_STARTUP_BRIDGE_DROPIN,
    )
    from codev_platform.ops.reindex_codegraph_startup_bridge_config import (
        CodegraphStartupBridgeSpec,
    )

    bridge = CodegraphStartupBridgeSpec(
        interpreter=Path("/runtime/current/bin/python"),
        bridge_path=Path("/controller/codegraph_startup_bridge.py"),
        port=18091,
    )
    (
        payload,
        expected_dropin,
        deployment_path,
        deployment_content,
        resume_path,
        guard_path,
        guard_content,
    ) = 配置codegraph_stage有效证明(
        module,
        monkeypatch,
        tmp_path,
    )
    contents = {
        deployment_path.as_posix(): deployment_content,
        CODEGRAPH_STARTUP_BRIDGE_DROPIN.as_posix(): bridge.dropin_content,
        resume_path.as_posix(): expected_dropin,
        guard_path.as_posix(): guard_content,
    }
    monkeypatch.setattr(
        module,
        "_run_systemctl_command",
        lambda _command: SimpleNamespace(
            returncode=0,
            stdout=属性输出(
                "/usr/local/lib/systemd/system/codev-mcp-codegraph.service",
                dropins=" ".join(contents),
                command=shlex.join(bridge.exec_start),
            ),
        ),
    )
    monkeypatch.setattr(
        module,
        "default_drop_in_snapshot_reader",
        lambda path: 目标原像(module, contents[path.as_posix()]),
    )

    module.default_staged_effective_unit_payload_verifier(
        (payload,),
        resume_dropin_content=expected_dropin,
        codegraph_startup_bridge_dropin_content=bridge.dropin_content,
        codegraph_effective_exec_start=bridge.exec_start,
    )


def test_stage有效证明拒绝额外未知dropin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module
    from codev_platform.core.systemd_maintenance_contract import (
        CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH,
    )
    from codev_platform.ops.reindex_codegraph_resume_contract import (
        CODEGRAPH_RESUME_DROPIN,
    )
    from codev_platform.runtime_systemd_gate_contract import deployment_guard_drop_in_path

    extra = "/etc/systemd/system/codev-mcp-codegraph.service.d/99-unknown.conf"
    deployment_path = deployment_guard_drop_in_path("codev-mcp-codegraph.service")
    (
        payload,
        expected_dropin,
        _deployment_path,
        _deployment_content,
        _resume_path,
        _guard_path,
        _guard_content,
    ) = 配置codegraph_stage有效证明(
        module,
        monkeypatch,
        tmp_path,
        dropins=(
            f"{deployment_path.as_posix()} {CODEGRAPH_RESUME_DROPIN.as_posix()} "
            f"{CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH.as_posix()} {extra}"
        ),
    )
    monkeypatch.setattr(
        module,
        "default_drop_in_snapshot_reader",
        lambda _path: pytest.fail("路径集合不一致时不得读取 drop-in"),
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="路径集合"):
        module.default_staged_effective_unit_payload_verifier(
            (payload,),
            resume_dropin_content=expected_dropin,
            allow_reindex_local_dropins=True,
        )


@pytest.mark.parametrize(
    ("mode", "uid", "gid"),
    ((0o640, 0, 0), (0o644, 1000, 0), (0o644, 0, 1000)),
)
def test_stage有效证明拒绝恢复dropin元数据漂移(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: int,
    uid: int,
    gid: int,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module

    (
        payload,
        expected_dropin,
        deployment_path,
        deployment_content,
        resume_path,
        _guard_path,
        guard_content,
    ) = 配置codegraph_stage有效证明(module, monkeypatch, tmp_path)
    monkeypatch.setattr(
        module,
        "default_drop_in_snapshot_reader",
        lambda path: 目标原像(
            module,
            (
                expected_dropin
                if path.as_posix() == resume_path.as_posix()
                else (
                    deployment_content
                    if path.as_posix() == deployment_path.as_posix()
                    else guard_content
                )
            ),
            mode=mode if path.as_posix() == resume_path.as_posix() else 0o644,
            uid=uid if path.as_posix() == resume_path.as_posix() else 0,
            gid=gid if path.as_posix() == resume_path.as_posix() else 0,
        ),
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="原像"):
        module.default_staged_effective_unit_payload_verifier(
            (payload,),
            resume_dropin_content=expected_dropin,
        )


def test_stage有效证明拒绝永久guard缺失或内容漂移(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module
    from codev_platform.ops.reindex_codegraph_resume_contract import (
        CODEGRAPH_RESUME_DROPIN,
    )
    from codev_platform.runtime_systemd_gate_contract import deployment_guard_drop_in_path

    expected_deployment_path = deployment_guard_drop_in_path("codev-mcp-codegraph.service")

    (
        payload,
        expected_dropin,
        deployment_path,
        deployment_content,
        resume_path,
        guard_path,
        guard_content,
    ) = 配置codegraph_stage有效证明(
        module,
        monkeypatch,
        tmp_path,
        dropins=(f"{expected_deployment_path.as_posix()} {CODEGRAPH_RESUME_DROPIN.as_posix()}"),
    )
    with pytest.raises(module.SystemdInstallTransactionError, match="路径集合"):
        module.default_staged_effective_unit_payload_verifier(
            (payload,),
            resume_dropin_content=expected_dropin,
        )

    monkeypatch.setattr(
        module,
        "_run_systemctl_command",
        lambda _command: SimpleNamespace(
            returncode=0,
            stdout=属性输出(
                "/usr/local/lib/systemd/system/codev-mcp-codegraph.service",
                dropins=(
                    f"{deployment_path.as_posix()} {resume_path.as_posix()} {guard_path.as_posix()}"
                ),
                command=(
                    "/release/bin/python -I -m codev_platform.codegraph.server --http --port 18091"
                ),
            ),
        ),
    )
    monkeypatch.setattr(
        module,
        "default_drop_in_snapshot_reader",
        lambda path: 目标原像(
            module,
            (
                expected_dropin
                if path.as_posix() == resume_path.as_posix()
                else (
                    deployment_content
                    if path.as_posix() == deployment_path.as_posix()
                    else guard_content + b"# drift\n"
                )
            ),
        ),
    )
    with pytest.raises(module.SystemdInstallTransactionError, match="原像"):
        module.default_staged_effective_unit_payload_verifier(
            (payload,),
            resume_dropin_content=expected_dropin,
        )
