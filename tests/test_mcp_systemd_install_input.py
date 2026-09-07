"""全量 systemd 安装输入绑定测试。"""

from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_打开输入父目录后将最终dirfd所有权交给调用方(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """目录链中间描述符必须关闭，最终描述符不能在返回前被误关闭。"""
    from codev_platform import mcp_systemd_install_input as module

    calls: list[object] = []
    descriptors = iter((31, 32))

    def open_file(*_args, **_kwargs) -> int:
        return next(descriptors)

    monkeypatch.setattr(module.os, "open", open_file)
    monkeypatch.setattr(module.os, "supports_dir_fd", {open_file})
    monkeypatch.setattr(module.os, "O_DIRECTORY", 0x10000, raising=False)
    monkeypatch.setattr(module.os, "O_NOFOLLOW", 0x20000, raising=False)
    monkeypatch.setattr(module.os, "O_CLOEXEC", 0x80000, raising=False)
    monkeypatch.setattr(
        module.os,
        "fstat",
        lambda _descriptor: SimpleNamespace(st_mode=stat.S_IFDIR | 0o755),
    )
    monkeypatch.setattr(module.os, "close", lambda descriptor: calls.append(("close", descriptor)))

    descriptor = module._open_parent_directory(Path("/trusted/install-manifest.json"))

    assert descriptor == 32
    assert ("close", 31) in calls
    assert ("close", 32) not in calls


def test_源内容与已读取manifest摘要不一致时拒绝安装输入(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """源文件在 manifest 读取后被替换时，摘要必须阻止特权写入。"""
    import hashlib
    import json

    from codev_platform import mcp_systemd_install_input as module

    source = (tmp_path / "codev-mcp-codegraph.service").resolve()
    manifest_path = (tmp_path / "install-manifest.json").resolve()
    expected = b"[Service]\nExecStart=/old\n"
    manifest = json.dumps(
        {
            "version": 4,
            "runtime_revision": "1" * 40,
            "units": [
                {
                    "source": str(source),
                    "sha256": hashlib.sha256(expected).hexdigest(),
                    "enable": True,
                    "restart": True,
                    "activation_mode": "codegraph_state_machine",
                }
            ],
        }
    ).encode()
    calls: list[object] = []
    monkeypatch.setattr(module, "_open_parent_directory", lambda _path: 41)
    monkeypatch.setattr(
        module, "_close_quietly", lambda descriptor: calls.append(("close", descriptor))
    )
    contents = {"install-manifest.json": manifest, source.name: b"[Service]\nExecStart=/replaced\n"}
    monkeypatch.setattr(
        module,
        "_read_regular_file_at",
        lambda descriptor, name, _max_bytes: (
            calls.append(("read", descriptor, name)) or contents[name]
        ),
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="摘要不匹配"):
        module.load_verified_install_input(manifest_path)

    assert calls[:2] == [("read", 41, "install-manifest.json"), ("read", 41, source.name)]
    assert calls[-1] == ("close", 41)


@pytest.mark.parametrize("version", (2, 3))
def test_旧manifest必须失败关闭并要求重新生成安装包(version: int) -> None:
    """旧清单仍含已废弃启动语义，特权入口不得自行猜测。"""
    import json

    from codev_platform import mcp_systemd_install_input as module

    payload = json.dumps({"version": version, "units": []}).encode("utf-8")

    with pytest.raises(module.SystemdInstallTransactionError, match="重新生成安装包"):
        module._parse_manifest(payload)


@pytest.mark.parametrize("version", (True, [], "4", None))
def test_manifest版本类型异常时稳定失败关闭(version: object) -> None:
    import json

    from codev_platform import mcp_systemd_install_input as module

    payload = json.dumps({"version": version, "units": []}).encode("utf-8")

    with pytest.raises(module.SystemdInstallTransactionError, match="版本无效"):
        module._parse_manifest(payload)


def test_CodeGraph不能声明为reindex状态机所有(tmp_path: Path) -> None:
    """两个延迟激活状态机不能互相接管对方的 unit。"""
    import hashlib

    from codev_platform import mcp_systemd_install_input as module
    from codev_platform.mcp_systemd_install_contract import (
        SystemdUnitActivationMode,
        SystemdUnitInstallSpec,
    )

    content = b"[Service]\nExecStart=/usr/bin/true\n"
    with pytest.raises(module.SystemdInstallTransactionError, match="激活所有者"):
        SystemdUnitInstallSpec(
            source=(tmp_path / "codev-mcp-codegraph.service").resolve(),
            content_digest=hashlib.sha256(content).hexdigest(),
            enable=True,
            restart=True,
            activation_mode=SystemdUnitActivationMode.REINDEX_STATE_MACHINE,
        )


def test_v4清单拒绝错误的状态机所有者(tmp_path: Path) -> None:
    """外部清单不能绕过构造期的 unit 所有权约束。"""
    import hashlib
    import json

    from codev_platform import mcp_systemd_install_input as module

    content = b"[Service]\nExecStart=/usr/bin/true\n"
    payload = json.dumps(
        {
            "version": 4,
            "runtime_revision": "1" * 40,
            "units": [
                {
                    "source": str((tmp_path / "codev-mcp-codegraph.service").resolve()),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "enable": True,
                    "restart": True,
                    "activation_mode": "reindex_state_machine",
                }
            ],
        }
    ).encode("utf-8")

    with pytest.raises(module.SystemdInstallTransactionError, match="激活所有者"):
        module._parse_manifest(payload)
