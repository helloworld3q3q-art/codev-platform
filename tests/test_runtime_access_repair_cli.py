from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform.ops import runtime_access_repair as command
from codev_platform.runtime_current_access_repair import RuntimeCurrentAccessRepairProof
from codev_platform.runtime_service_process import ServiceAccount


_ACCOUNT = ServiceAccount(
    name="worker",
    uid=23456,
    gid=23457,
    home=Path("/srv/worker"),
)


def _proof(*, dry_run: bool) -> RuntimeCurrentAccessRepairProof:
    return RuntimeCurrentAccessRepairProof(
        access_profile="root-service-group-read-v1",
        service_uid=_ACCOUNT.uid,
        service_gid=_ACCOUNT.gid,
        base_id="a" * 64,
        release_id="b" * 64,
        base_entries=21,
        base_total_bytes=34,
        release_entries=13,
        release_total_bytes=55,
        dry_run=dry_run,
        target_user_evidence_sha256=None if dry_run else "c" * 64,
    )


def _args(*, yes: bool) -> SimpleNamespace:
    return SimpleNamespace(
        runtime_root=Path("/runtime"),
        service_user="worker",
        yes=yes,
        dry_run=not yes,
    )


def _ports(events: list[str], *, permitted: bool = True) -> SimpleNamespace:
    @contextmanager
    def permit():
        events.append("permit-enter")
        try:
            yield permitted
        finally:
            events.append("permit-exit")

    def repair(root: Path, *, account: ServiceAccount, dry_run: bool):
        assert root == Path("/runtime")
        assert account is _ACCOUNT
        events.append(f"repair:{dry_run}")
        return _proof(dry_run=dry_run)

    return SimpleNamespace(
        load_config=lambda: {"runtime": {"release_root": "/configured"}},
        release_root=lambda _config, explicit: Path(explicit),
        resolve_service_account=lambda user: _ACCOUNT if user == "worker" else None,
        maintenance_permit=permit,
        inspect_maintenance=lambda: events.append("inspect"),
        repair=repair,
    )


def test默认命令只执行dry_run且不读取维护窗口(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    events: list[str] = []
    monkeypatch.setattr(command, "_default_ports", lambda: _ports(events))
    monkeypatch.setattr(sys, "dont_write_bytecode", False)

    assert command.cmd_runtime_access_repair(_args(yes=False)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["result"]["dry_run"] is True
    assert sys.dont_write_bytecode is True
    assert events == ["repair:True"]


def test显式yes必须在维护窗口内完成前后检查(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    events: list[str] = []
    monkeypatch.setattr(command, "_default_ports", lambda: _ports(events))

    assert command.cmd_runtime_access_repair(_args(yes=True)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["result"]["dry_run"] is False
    assert events == [
        "permit-enter",
        "inspect",
        "repair:False",
        "inspect",
        "permit-exit",
    ]


def test维护窗口未授权时拒绝写入且不泄露路径(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        command,
        "_default_ports",
        lambda: _ports(events, permitted=False),
    )

    assert command.cmd_runtime_access_repair(_args(yes=True)) == 1

    payload = json.loads(capsys.readouterr().err)
    assert payload == {
        "error": "runtime_access_repair_failed",
        "kind": "runtime_access_repair",
        "status": "error",
    }
    assert events == ["permit-enter", "permit-exit"]
