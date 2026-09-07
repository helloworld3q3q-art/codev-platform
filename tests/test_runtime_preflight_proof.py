"""目标用户 systemd 预检证明测试。"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

import codev_platform.runtime_preflight_proof as proof
from codev_platform.core.runtime_models import RuntimeIdentity, SystemdRuntime
from codev_platform.mcp_systemd_install_contract import SystemdRuntimeBinding
from codev_platform.mcp_systemd_unit_registry import MANAGED_SYSTEMD_UNIT_NAMES
from codev_platform.runtime_preflight_proof import (
    TargetUserPreflightError,
    create_target_user_systemd_proof,
    main,
)


_RELEASE_ID = "a" * 64
_REVISION = "b" * 40
_ROOT = "/srv/codev-runtime"
_USER = "service-user"


def _rendered_units() -> dict[str, str]:
    return {name: f"[Unit]\nDescription={name}\n" for name in MANAGED_SYSTEMD_UNIT_NAMES}


def test_proof_uses_one_snapshot_then_hashes_exact_twelve_and_runs_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    cfg = {"marker": "single-snapshot"}
    identity = object()
    requirements = (object(),)
    units = _rendered_units()

    monkeypatch.setattr(proof, "runtime_identity", lambda: (events.append("identity"), identity)[1])
    monkeypatch.setattr(
        proof,
        "_verify_release_identity",
        lambda actual, runtime, release_id, revision: events.append(
            ("verify", actual, runtime, release_id, revision)
        ),
    )
    monkeypatch.setattr(
        proof,
        "_current_user_name",
        lambda: (events.append("current_user"), _USER)[1],
    )
    working_directory = PurePosixPath("/srv/service-home")
    monkeypatch.setattr(
        proof,
        "resolve_service_account",
        lambda user: SimpleNamespace(name=user, home=working_directory),
    )

    load_count = 0

    def load_once() -> dict[str, object]:
        nonlocal load_count
        load_count += 1
        events.append("load_config")
        return cfg

    monkeypatch.setattr(proof, "load_config", load_once)

    def render(
        actual_cfg: dict,
        user: str,
        *,
        runtime: SystemdRuntime,
        runtime_binding: SystemdRuntimeBinding,
        working_directory: str,
    ) -> dict[str, str]:
        events.append(("render", actual_cfg, user, runtime, runtime_binding, working_directory))
        return units

    monkeypatch.setattr(proof, "render_managed_systemd_units", render)
    monkeypatch.setattr(
        proof,
        "permission_requirements",
        lambda actual_cfg, runtime: (
            events.append(("requirements", actual_cfg, runtime)),
            requirements,
        )[1],
    )

    def probe(actual_requirements: tuple[object, ...], deadline) -> None:
        events.append(
            (
                "probe",
                actual_requirements,
                deadline.expires_at,
                deadline.monotonic(),
            )
        )

    monkeypatch.setattr(proof, "probe_current_user_until", probe)

    result = create_target_user_systemd_proof(
        release_root=_ROOT,
        expected_release_id=_RELEASE_ID,
        expected_runtime_revision=_REVISION,
        target_user=_USER,
        timeout_sec=30.0,
        monotonic=lambda: 10.0,
    )

    runtime = SystemdRuntime(Path(_ROOT))
    binding = SystemdRuntimeBinding(
        release_root=_ROOT,
        expected_release_id=_RELEASE_ID,
        target_user=_USER,
    )
    assert load_count == 1
    assert events == [
        "identity",
        ("verify", identity, runtime, _RELEASE_ID, _REVISION),
        "current_user",
        "load_config",
        ("render", cfg, _USER, runtime, binding, "/srv/service-home"),
        ("requirements", cfg, runtime),
        ("probe", requirements, 40.0, 10.0),
    ]
    assert result == {
        "release_id": _RELEASE_ID,
        "runtime_revision": _REVISION,
        "schema_version": 1,
        "target_user": _USER,
        "units": [
            {
                "name": name,
                "sha256": hashlib.sha256(units[name].encode("utf-8")).hexdigest(),
            }
            for name in sorted(MANAGED_SYSTEMD_UNIT_NAMES)
        ],
    }
    assert len(result["units"]) == 12
    assert all(set(item) == {"name", "sha256"} for item in result["units"])


def test_identity_failure_stops_before_uid_and_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        proof,
        "runtime_identity",
        lambda: (events.append("identity"), SimpleNamespace(mode="editable"))[1],
    )
    monkeypatch.setattr(
        proof,
        "_current_user_name",
        lambda: pytest.fail("身份失败后不得查询目标用户"),
    )
    monkeypatch.setattr(proof, "load_config", lambda: pytest.fail("身份失败后不得加载配置"))

    with pytest.raises(TargetUserPreflightError) as caught:
        create_target_user_systemd_proof(
            release_root=_ROOT,
            expected_release_id=_RELEASE_ID,
            expected_runtime_revision=_REVISION,
            target_user=_USER,
        )

    assert caught.value.code == "runtime_identity_mismatch"
    assert events == ["identity"]
    assert _ROOT not in str(caught.value)


def test_uid_user_mismatch_stops_before_config(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(proof, "runtime_identity", lambda: object())
    monkeypatch.setattr(
        proof,
        "_verify_release_identity",
        lambda *_args: events.append("identity_verified"),
    )
    monkeypatch.setattr(proof, "_current_user_name", lambda: "other-user")
    monkeypatch.setattr(proof, "load_config", lambda: pytest.fail("用户错配后不得加载配置"))

    with pytest.raises(TargetUserPreflightError) as caught:
        create_target_user_systemd_proof(
            release_root=_ROOT,
            expected_release_id=_RELEASE_ID,
            expected_runtime_revision=_REVISION,
            target_user=_USER,
        )

    assert caught.value.code == "target_user_mismatch"
    assert events == ["identity_verified"]
    assert "other-user" not in str(caught.value)


def test_managed_unit_drift_fails_before_permission_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(proof, "runtime_identity", lambda: object())
    monkeypatch.setattr(proof, "_verify_release_identity", lambda *_args: None)
    monkeypatch.setattr(proof, "_current_user_name", lambda: _USER)
    monkeypatch.setattr(proof, "load_config", lambda: {})
    units = _rendered_units()
    units.pop(next(iter(units)))
    monkeypatch.setattr(proof, "render_managed_systemd_units", lambda *_args, **_kwargs: units)
    monkeypatch.setattr(
        proof,
        "permission_requirements",
        lambda *_args: pytest.fail("unit 集合漂移后不得执行权限探针"),
    )

    with pytest.raises(TargetUserPreflightError) as caught:
        create_target_user_systemd_proof(
            release_root=_ROOT,
            expected_release_id=_RELEASE_ID,
            expected_runtime_revision=_REVISION,
            target_user=_USER,
        )

    assert caught.value.code == "preflight_failed"


def test_registry_size_drift_cannot_redefine_fixed_twelve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drifted = {f"codev-drift-{index}.service": "content" for index in range(13)}
    monkeypatch.setattr(proof, "MANAGED_SYSTEMD_UNIT_NAMES", frozenset(drifted))

    with pytest.raises(ValueError, match="集合漂移"):
        proof._unit_proofs(drifted)


def test_overall_deadline_covers_identity_before_user_and_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 0.0
    monkeypatch.setattr(proof, "runtime_identity", lambda: object())

    def verify(*_args: object) -> None:
        nonlocal now
        now = 2.0

    monkeypatch.setattr(proof, "_verify_release_identity", verify)
    monkeypatch.setattr(
        proof,
        "_current_user_name",
        lambda: pytest.fail("整体时限到期后不得继续"),
    )

    with pytest.raises(TargetUserPreflightError) as caught:
        create_target_user_systemd_proof(
            release_root=_ROOT,
            expected_release_id=_RELEASE_ID,
            expected_runtime_revision=_REVISION,
            target_user=_USER,
            timeout_sec=1.0,
            monotonic=lambda: now,
        )

    assert caught.value.code == "preflight_timeout"


@pytest.mark.skipif(os.name != "posix", reason="current release 符号链接身份仅在 WSL/Linux 验证")
def test_release_identity_proof_matches_current_interpreter_completely(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    release = root / "releases" / _RELEASE_ID
    python = release / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    (root / "current").symlink_to(Path("releases") / _RELEASE_ID, target_is_directory=True)
    identity = RuntimeIdentity(
        mode="release",
        runtime_revision=_REVISION,
        release_id=_RELEASE_ID,
        wheel_sha256="c" * 64,
        base_id="d" * 64,
        base_requirements_sha256="e" * 64,
        interpreter_realpath=str(python.resolve()),
        environment_prefix=str((release / "venv").resolve()),
        source_root=None,
    )

    proof._verify_release_identity(
        identity,
        SystemdRuntime(root),
        _RELEASE_ID,
        _REVISION,
    )

    with pytest.raises(ValueError, match="身份"):
        proof._verify_release_identity(
            replace(identity, runtime_revision="f" * 40),
            SystemdRuntime(root),
            _RELEASE_ID,
            _REVISION,
        )


def test_cli_success_is_one_canonical_json_proof(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    expected = {
        "release_id": _RELEASE_ID,
        "runtime_revision": _REVISION,
        "schema_version": 1,
        "target_user": _USER,
        "units": [],
    }
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        proof,
        "create_target_user_systemd_proof",
        lambda **kwargs: (calls.append(kwargs), expected)[1],
    )

    assert (
        main(
            [
                "--release-root",
                _ROOT,
                "--expected-release-id",
                _RELEASE_ID,
                "--expected-runtime-revision",
                _REVISION,
                "--target-user",
                _USER,
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert (
        captured.out
        == json.dumps(
            expected,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    assert captured.err == ""
    assert calls == [
        {
            "release_root": _ROOT,
            "expected_release_id": _RELEASE_ID,
            "expected_runtime_revision": _REVISION,
            "target_user": _USER,
        }
    ]


@pytest.mark.parametrize(
    "argv",
    (
        [],
        [
            "--release-root",
            "/secret/runtime",
            "--expected-release-id",
            _RELEASE_ID,
            "--expected-runtime-revision",
            _REVISION,
            "--target-user",
            "first-user",
            "--target-user",
            "second-user",
        ],
        ["--unknown", "secret-value"],
        [
            "--release-root",
            "/secret/runtime",
            "--expected-release-id",
            "secret-release-id",
            "--expected-runtime-revision",
            _REVISION,
            "--target-user",
            _USER,
        ],
    ),
)
def test_cli_argument_failure_is_fixed_and_never_echoes_values(
    argv: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(argv) == 2

    raw = capsys.readouterr().out
    assert json.loads(raw) == {"error_code": "invalid_arguments", "schema_version": 1}
    assert "secret" not in raw
    assert "first-user" not in raw
    assert "second-user" not in raw


def test_cli_runtime_failure_emits_only_fixed_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(**_kwargs: object) -> dict[str, object]:
        raise TargetUserPreflightError("runtime_identity_mismatch")

    monkeypatch.setattr(proof, "create_target_user_systemd_proof", fail)

    assert (
        main(
            [
                "--release-root",
                "/secret/runtime",
                "--expected-release-id",
                _RELEASE_ID,
                "--expected-runtime-revision",
                _REVISION,
                "--target-user",
                _USER,
            ]
        )
        == 1
    )

    raw = capsys.readouterr().out
    assert json.loads(raw) == {
        "error_code": "runtime_identity_mismatch",
        "schema_version": 1,
    }
    assert "/secret/runtime" not in raw


@pytest.mark.parametrize("outcome", ("success", "failure"))
def test_cli_discards_dependency_output_so_values_cannot_leak(
    outcome: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def noisy(**_kwargs: object) -> dict[str, object]:
        print("secret-path=/do/not/expose")
        print("secret-dsn=postgresql://private", file=sys.stderr)
        if outcome == "failure":
            raise TargetUserPreflightError("preflight_failed")
        return {
            "release_id": _RELEASE_ID,
            "runtime_revision": _REVISION,
            "schema_version": 1,
            "target_user": _USER,
            "units": [],
        }

    monkeypatch.setattr(proof, "create_target_user_systemd_proof", noisy)
    exit_code = main(
        [
            "--release-root",
            _ROOT,
            "--expected-release-id",
            _RELEASE_ID,
            "--expected-runtime-revision",
            _REVISION,
            "--target-user",
            _USER,
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == (0 if outcome == "success" else 1)
    assert "secret" not in captured.out
    assert captured.err == ""
