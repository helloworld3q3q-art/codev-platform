"""CodeGraph M1 启动 bridge drop-in 的受控生命周期测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def _规格(module):
    return module.CodegraphStartupBridgeSpec(
        interpreter=Path("/runtime/current/bin/python"),
        bridge_path=Path("/controller/codegraph_startup_bridge.py"),
        port=19091,
    )


def _原像(module, content: bytes):
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        RootOwnedRegularFileSnapshot,
    )

    return RootOwnedRegularFileSnapshot(content, 0o644, 0, 0)


def _服务组只读原像(content: bytes):
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        RootOwnedRegularFileSnapshot,
    )

    return RootOwnedRegularFileSnapshot(content, 0o640, 0, 1000)


def _适配器(module, spec):
    files = {Path(spec.bridge_path): _原像(module, b"bridge source\n")}
    events: list[object] = []

    def read(path: Path):
        return files.get(Path(path))

    def write(path: Path, content: bytes, mode: int) -> None:
        events.append(("write", path, content, mode))
        files[Path(path)] = _原像(module, content)

    def remove(path: Path) -> bool:
        events.append(("remove", path))
        return files.pop(Path(path), None) is not None

    def reload() -> None:
        events.append("reload")

    return files, events, read, write, remove, reload


def test_安装bridge只接受受信源并立即重载复证() -> None:
    from codev_platform.ops import reindex_codegraph_startup_bridge_config as module

    spec = _规格(module)
    files, events, read, write, remove, reload = _适配器(module, spec)

    module.install_codegraph_startup_bridge(
        spec,
        platform_name="linux",
        effective_user_id=lambda: 0,
        snapshot_reader=read,
        file_writer=write,
        file_remover=remove,
        systemd_reloader=reload,
    )

    assert files[module.CODEGRAPH_STARTUP_BRIDGE_DROPIN] == spec.snapshot
    assert events == [
        ("write", module.CODEGRAPH_STARTUP_BRIDGE_DROPIN, spec.dropin_content, 0o644),
        "reload",
    ]


def test_安装bridge接受受管发布的服务组只读源() -> None:
    from codev_platform.ops import reindex_codegraph_startup_bridge_config as module

    spec = _规格(module)
    files, events, read, write, remove, reload = _适配器(module, spec)
    files[Path(spec.bridge_path)] = _服务组只读原像(b"bridge source\n")

    module.install_codegraph_startup_bridge(
        spec,
        platform_name="linux",
        effective_user_id=lambda: 0,
        snapshot_reader=read,
        file_writer=write,
        file_remover=remove,
        systemd_reloader=reload,
    )

    assert files[module.CODEGRAPH_STARTUP_BRIDGE_DROPIN] == spec.snapshot
    assert events == [
        ("write", module.CODEGRAPH_STARTUP_BRIDGE_DROPIN, spec.dropin_content, 0o644),
        "reload",
    ]


@pytest.mark.parametrize(
    "untrusted",
    (
        SimpleNamespace(content=b"bridge source\n", mode=0o644, uid=0, gid=0),
        _服务组只读原像(b""),
    ),
    ids=("非受管原像", "空原像"),
)
def test_安装bridge拒绝非受管或空的源原像(untrusted: object) -> None:
    from codev_platform.ops import reindex_codegraph_startup_bridge_config as module

    spec = _规格(module)
    files, events, _read, write, remove, reload = _适配器(module, spec)

    def read(path: Path):
        if Path(path) == Path(spec.bridge_path):
            return untrusted
        return files.get(Path(path))

    with pytest.raises(module.CodegraphStartupBridgeError, match="源文件不受信任"):
        module.install_codegraph_startup_bridge(
            spec,
            platform_name="linux",
            effective_user_id=lambda: 0,
            snapshot_reader=read,
            file_writer=write,
            file_remover=remove,
            systemd_reloader=reload,
        )

    assert module.CODEGRAPH_STARTUP_BRIDGE_DROPIN not in files
    assert events == []


def test_bridge启动命令直接执行受信脚本且不携带Python_c文本() -> None:
    from codev_platform.ops import reindex_codegraph_startup_bridge_config as module

    spec = _规格(module)

    assert spec.exec_start == (
        "/runtime/current/bin/python",
        "-I",
        "-B",
        "/controller/codegraph_startup_bridge.py",
        "--http",
        "--port",
        "19091",
    )
    assert spec.dropin_content == (
        b"[Service]\n"
        b"ExecStart=\n"
        b"ExecStart=/runtime/current/bin/python -I -B "
        b"/controller/codegraph_startup_bridge.py --http --port 19091\n"
    )
    assert b" -c " not in spec.dropin_content


def test_M0清理只删除精确bridge且对缺失幂等() -> None:
    from codev_platform.ops import reindex_codegraph_startup_bridge_config as module

    spec = _规格(module)
    files, events, read, write, remove, reload = _适配器(module, spec)
    files[module.CODEGRAPH_STARTUP_BRIDGE_DROPIN] = spec.snapshot

    module.clear_codegraph_startup_bridge(
        spec,
        platform_name="linux",
        effective_user_id=lambda: 0,
        snapshot_reader=read,
        file_writer=write,
        file_remover=remove,
        systemd_reloader=reload,
    )
    module.clear_codegraph_startup_bridge(
        spec,
        platform_name="linux",
        effective_user_id=lambda: 0,
        snapshot_reader=read,
        file_writer=write,
        file_remover=remove,
        systemd_reloader=reload,
    )

    assert module.CODEGRAPH_STARTUP_BRIDGE_DROPIN not in files
    assert events == [("remove", module.CODEGRAPH_STARTUP_BRIDGE_DROPIN), "reload"]


def test_M0清理拒绝覆盖未知bridge文件() -> None:
    from codev_platform.ops import reindex_codegraph_startup_bridge_config as module

    spec = _规格(module)
    files, events, read, write, remove, reload = _适配器(module, spec)
    unexpected = _原像(module, b"[Service]\nExecStart=/unknown\n")
    files[module.CODEGRAPH_STARTUP_BRIDGE_DROPIN] = unexpected

    with pytest.raises(module.CodegraphStartupBridgeError, match="原像无法证明"):
        module.clear_codegraph_startup_bridge(
            spec,
            platform_name="linux",
            effective_user_id=lambda: 0,
            snapshot_reader=read,
            file_writer=write,
            file_remover=remove,
            systemd_reloader=reload,
        )

    assert files[module.CODEGRAPH_STARTUP_BRIDGE_DROPIN] == unexpected
    assert events == []


def test_删除bridge重载失败时恢复精确原像() -> None:
    from codev_platform.ops import reindex_codegraph_startup_bridge_config as module

    spec = _规格(module)
    files, events, read, write, remove, _reload = _适配器(module, spec)
    files[module.CODEGRAPH_STARTUP_BRIDGE_DROPIN] = spec.snapshot
    reload_count = 0

    def reload() -> None:
        nonlocal reload_count
        reload_count += 1
        events.append("reload")
        if reload_count == 1:
            raise RuntimeError("reload failed")

    with pytest.raises(module.CodegraphStartupBridgeError, match="已恢复原像"):
        module.remove_codegraph_startup_bridge(
            spec,
            platform_name="linux",
            effective_user_id=lambda: 0,
            snapshot_reader=read,
            file_writer=write,
            file_remover=remove,
            systemd_reloader=reload,
        )

    assert files[module.CODEGRAPH_STARTUP_BRIDGE_DROPIN] == spec.snapshot
    assert events == [
        ("remove", module.CODEGRAPH_STARTUP_BRIDGE_DROPIN),
        "reload",
        ("write", module.CODEGRAPH_STARTUP_BRIDGE_DROPIN, spec.dropin_content, 0o644),
        "reload",
    ]
