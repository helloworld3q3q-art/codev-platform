from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform.ops import runtime_staged_release_access as command
from codev_platform.runtime_service_process import ServiceAccount
from codev_platform.runtime_staged_release_access import RuntimeStagedReleaseAccessProof


_RELEASE_ID = "b" * 64
_ACCOUNT = ServiceAccount(
    name="worker",
    uid=23456,
    gid=23457,
    home=Path("/srv/worker"),
)


def _args(*, yes: bool) -> SimpleNamespace:
    return SimpleNamespace(
        runtime_root=Path("/runtime"),
        release_id=_RELEASE_ID,
        service_user="worker",
        yes=yes,
        dry_run=not yes,
    )


def _proof(*, dry_run: bool) -> RuntimeStagedReleaseAccessProof:
    return RuntimeStagedReleaseAccessProof(
        access_profile="root-service-group-read-v1",
        service_uid=_ACCOUNT.uid,
        service_gid=_ACCOUNT.gid,
        base_id="a" * 64,
        release_id=_RELEASE_ID,
        dry_run=dry_run,
        target_user_evidence_sha256=None if dry_run else "c" * 64,
    )


def _ports(events: list[str]) -> SimpleNamespace:
    def publish(
        root: Path,
        *,
        account: ServiceAccount,
        release_id: str,
        dry_run: bool,
    ) -> RuntimeStagedReleaseAccessProof:
        assert (root, account, release_id) == (Path("/runtime"), _ACCOUNT, _RELEASE_ID)
        events.append(f"publish:{dry_run}")
        return _proof(dry_run=dry_run)

    return SimpleNamespace(
        load_config=lambda: {"runtime": {"release_root": "/configured"}},
        release_root=lambda _config, explicit: Path(explicit),
        resolve_service_account=lambda user: _ACCOUNT if user == "worker" else None,
        publish=publish,
    )


def test默认只做dry_run且不写字节码(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    events: list[str] = []
    monkeypatch.setattr(command, "_default_ports", lambda: _ports(events))
    monkeypatch.setattr(sys, "dont_write_bytecode", False)

    assert command.cmd_runtime_staged_release_access(_args(yes=False)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["result"]["dry_run"] is True
    assert sys.dont_write_bytecode is True
    assert events == ["publish:True"]


def test显式确认才发布服务访问投影(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    events: list[str] = []
    monkeypatch.setattr(command, "_default_ports", lambda: _ports(events))

    assert command.cmd_runtime_staged_release_access(_args(yes=True)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["result"]["dry_run"] is False
    assert events == ["publish:False"]


def test失败回执不泄露运行时路径(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(*_args, **_kwargs):
        raise RuntimeError("/secret/runtime token=abc")

    ports = _ports([])
    ports.publish = fail
    monkeypatch.setattr(command, "_default_ports", lambda: ports)

    assert command.cmd_runtime_staged_release_access(_args(yes=True)) == 1

    payload = json.loads(capsys.readouterr().err)
    assert payload == {
        "error": "runtime_staged_release_access_failed",
        "kind": "runtime_staged_release_access",
        "status": "error",
    }
    assert "secret" not in json.dumps(payload)
