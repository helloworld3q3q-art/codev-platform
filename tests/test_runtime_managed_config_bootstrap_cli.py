"""受管配置 bootstrap CLI 的确认与脱敏边界测试。"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from codev_platform.ops import runtime_managed_config_bootstrap as command
from codev_platform.runtime_managed_configuration import (
    ManagedConfigurationBootstrapReceipt,
)


def _args(*, yes: bool) -> SimpleNamespace:
    return SimpleNamespace(service_user="worker", yes=yes, dry_run=not yes)


def _receipt(state: str) -> ManagedConfigurationBootstrapReceipt:
    return ManagedConfigurationBootstrapReceipt(
        config_sha256="a" * 64,
        environment_sha256="b" * 64,
        evidence_sha256="c" * 64,
        state=state,
    )


def test_默认命令传递dry_run且禁止root字节码写入(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        command,
        "_runner",
        lambda: lambda *, service_user, apply: (
            calls.append((service_user, apply)),
            _receipt("ready"),
        )[1],
    )
    monkeypatch.setattr(sys, "dont_write_bytecode", False)

    assert command.cmd_runtime_managed_config_bootstrap(_args(yes=False)) == 0

    assert calls == [("worker", False)]
    assert sys.dont_write_bytecode is True
    assert json.loads(capsys.readouterr().out)["result"]["state"] == "ready"


def test_确认参数才传递写入许可(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(
        command,
        "_runner",
        lambda: lambda *, service_user, apply: (
            calls.append(apply),
            _receipt("published"),
        )[1],
    )

    assert command.cmd_runtime_managed_config_bootstrap(_args(yes=True)) == 0

    assert calls == [True]
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_底层异常只输出固定错误码不泄露配置细节(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(**_kwargs):
        raise RuntimeError("/secret/config token=abc")

    monkeypatch.setattr(command, "_runner", lambda: fail)

    assert command.cmd_runtime_managed_config_bootstrap(_args(yes=True)) == 1

    payload = json.loads(capsys.readouterr().err)
    assert payload == {
        "error": "runtime_managed_config_bootstrap_failed",
        "kind": "runtime_managed_config_bootstrap",
        "status": "error",
    }


def test_回执字段非法时同样不会输出其内容(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        command,
        "_runner",
        lambda: lambda **_kwargs: ManagedConfigurationBootstrapReceipt(
            config_sha256="token=abc",
            environment_sha256="b" * 64,
            evidence_sha256="c" * 64,
            state="ready",
        ),
    )

    assert command.cmd_runtime_managed_config_bootstrap(_args(yes=False)) == 1

    serialized = capsys.readouterr().err
    assert "token=abc" not in serialized
    assert json.loads(serialized)["error"] == "runtime_managed_config_bootstrap_failed"
