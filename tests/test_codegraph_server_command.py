"""CodeGraph 外部命令入口的固定词法策略测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_未配置时使用固定裸命令(monkeypatch) -> None:
    from codev_platform.codegraph.server_command import resolve_codegraph_command

    monkeypatch.delenv("CODEGRAPH_CMD", raising=False)

    assert resolve_codegraph_command() == "codegraph"


def test_只接受真实绝对文件路径(tmp_path: Path, monkeypatch) -> None:
    from codev_platform.codegraph.server_command import resolve_codegraph_command

    executable = (tmp_path / "codegraph").resolve()
    executable.write_bytes(b"binary")
    executable.chmod(0o700)
    monkeypatch.setenv("CODEGRAPH_CMD", executable.as_posix())

    assert resolve_codegraph_command() == str(executable)


def test_含shell语义或普通相对路径时失败关闭到默认值(monkeypatch) -> None:
    from codev_platform.codegraph.server_command import resolve_codegraph_command

    for value in ("codegraph;evil", "bin/codegraph"):
        monkeypatch.setenv("CODEGRAPH_CMD", value)
        assert resolve_codegraph_command() == "codegraph"


@pytest.mark.skipif(os.name == "nt", reason="Windows 不使用 POSIX 执行位")
def test_绝对普通文件没有执行位时启动前失败关闭(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from codev_platform.codegraph.server_command import resolve_codegraph_command

    executable = (tmp_path / "codegraph-secret-path").resolve()
    executable.write_bytes(b"binary")
    executable.chmod(0o600)
    monkeypatch.setenv("CODEGRAPH_CMD", executable.as_posix())

    assert resolve_codegraph_command() == "codegraph"


def test_非法命令告警不回显原始环境值(monkeypatch, capsys) -> None:
    from codev_platform.codegraph.server_command import resolve_codegraph_command

    secret = "token=不应回显;evil"
    monkeypatch.setenv("CODEGRAPH_CMD", secret)

    assert resolve_codegraph_command() == "codegraph"
    assert secret not in capsys.readouterr().err
