"""root-fd worker systemd MainPID 与收口属性的窄契约回归。"""

from __future__ import annotations

import subprocess

import pytest

import codev_platform.runtime_worker_scope_control as control


def testscope控制属性必须与主进程收口契约完全一致(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不能仅因 unit active 就放行业务 operation。"""
    expected = _scope_properties(321)
    monkeypatch.setattr(control, "_run_systemctl", lambda *_args, **_kwargs: _completed(expected))

    control.verify_scope_control("codev-rootfd-0123456789ab.service", 321)


@pytest.mark.parametrize(
    ("property_name", "unexpected"),
    (
        ("MainPID", "322"),
        ("ExitType", "cgroup"),
        ("KillSignal", "15"),
        ("Restart", "always"),
        ("RemainAfterExit", "yes"),
    ),
)
def testscope控制属性漂移会在operation前拒绝(
    monkeypatch: pytest.MonkeyPatch,
    property_name: str,
    unexpected: str,
) -> None:
    """MainPID 或 cgroup 收口策略任一漂移都不能继续执行。"""
    properties = _scope_properties(321)
    properties[property_name] = unexpected
    monkeypatch.setattr(control, "_run_systemctl", lambda *_args, **_kwargs: _completed(properties))

    with pytest.raises(control.RuntimeWorkerScopeControlError, match="控制面"):
        control.verify_scope_control("codev-rootfd-0123456789ab.service", 321)


def _scope_properties(main_process_id: int) -> dict[str, str]:
    return {
        "ActiveState": "active",
        "MainPID": str(main_process_id),
        "KillMode": "control-group",
        "ExitType": "main",
        "KillSignal": "9",
        "SendSIGKILL": "yes",
        "Restart": "no",
        "RemainAfterExit": "no",
    }


def _completed(properties: dict[str, str]) -> subprocess.CompletedProcess[bytes]:
    payload = "\n".join(f"{name}={properties[name]}" for name in control.SCOPE_PROPERTY_NAMES)
    return subprocess.CompletedProcess(("systemctl",), 0, payload.encode("ascii"), b"")
