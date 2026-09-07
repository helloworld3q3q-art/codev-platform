"""版本化运行时 CLI 的解析、编排与无秘密输出契约。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform.core.runtime_models import (
    RUNTIME_ACCESS_PROFILE,
    ActivationResult,
    BaseMetadata,
    ReleaseCandidate,
    ReleaseMetadata,
    RequirementsLockInfo,
    RuntimeAbi,
    RuntimeIdentity,
)
from codev_platform.ops import runtime as runtime_cli
from codev_platform.runtime_errors import RuntimeIdCollisionError


BASE_ID = "1" * 64
RELEASE_ID = "2" * 64
ROLLBACK_RELEASE_ID = "8" * 64
REVISION = "a" * 40
WHEEL_SHA = "3" * 64
REQUIREMENTS_SHA = "4" * 64
MANIFEST_SHA = "5" * 64
BASE_METADATA_SHA = "6" * 64
FREEZE_SHA = "7" * 64


class _OperationFailure(RuntimeError):
    """模拟公开运行时函数的固定边界错误。"""


class _CollisionFailure(_OperationFailure):
    """模拟内容地址碰撞。"""


def _abi() -> RuntimeAbi:
    return RuntimeAbi(
        implementation="cpython",
        python_version="3.12.4",
        cache_tag="cpython-312",
        soabi="cpython-312-x86_64-linux-gnu",
        platform_tag="linux-x86_64",
        machine="x86_64",
    )


def _lock_info() -> RequirementsLockInfo:
    return RequirementsLockInfo(
        requirements_sha256=REQUIREMENTS_SHA,
        approved_index_url="https://download.pytorch.org/whl/cu128",
        pin_count=2,
        artifact_manifest_sha256=MANIFEST_SHA,
        cuda_tags=frozenset({"cu128"}),
    )


def _base() -> BaseMetadata:
    return BaseMetadata(
        schema_version=3,
        access_profile=RUNTIME_ACCESS_PROFILE,
        base_id=BASE_ID,
        requirements_sha256=REQUIREMENTS_SHA,
        approved_index_url="https://download.pytorch.org/whl/cu128",
        artifact_manifest_sha256=MANIFEST_SHA,
        freeze_sha256=FREEZE_SHA,
        purelib_inventory_sha256="f" * 64,
        abi=_abi(),
        created_at="2026-07-17T00:00:00Z",
        python_relative="venv/bin/python",
        purelib_relative="venv/lib/python3.12/site-packages",
        bin_relative="venv/bin",
        lock_relative="requirements.lock",
    )


def _candidate() -> ReleaseCandidate:
    return ReleaseCandidate(
        schema_version=1,
        runtime_revision=REVISION,
        wheel_name="codev_platform-1.0-py3-none-any.whl",
        wheel_sha256=WHEEL_SHA,
    )


def _release() -> ReleaseMetadata:
    return ReleaseMetadata(
        schema_version=1,
        release_id=RELEASE_ID,
        runtime_revision=REVISION,
        wheel_sha256=WHEEL_SHA,
        base_id=BASE_ID,
        base_requirements_sha256=REQUIREMENTS_SHA,
        base_metadata_sha256=BASE_METADATA_SHA,
        app_freeze_sha256=FREEZE_SHA,
        created_at="2026-07-17T00:00:00Z",
        python_relative="venv/bin/python",
        purelib_relative="venv/lib/python3.12/site-packages",
        base_link_relative="base",
        base_pth_relative="venv/lib/python3.12/site-packages/codev_platform_base.pth",
    )


def _identity() -> RuntimeIdentity:
    return RuntimeIdentity(
        mode="release",
        runtime_revision=REVISION,
        release_id=RELEASE_ID,
        wheel_sha256=WHEEL_SHA,
        base_id=BASE_ID,
        base_requirements_sha256=REQUIREMENTS_SHA,
        interpreter_realpath="/secret/runtime/venv/bin/python",
        environment_prefix="/secret/runtime/venv",
        source_root=None,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="cmd", required=True)
    runtime_cli.register(subparsers)
    return parser


def _ports(**overrides):
    defaults = {
        "load_config": lambda: {"runtime": {"release_root": "/configured/runtime"}},
        "release_root": lambda _cfg, explicit=None: explicit or Path("/configured/runtime"),
        "build_hashed_lock": lambda *_args: _lock_info(),
        "build_base": lambda *_args: _base(),
        "build_candidate": lambda *_args: (
            Path("/artifacts/app.whl"),
            Path("/artifacts/app.whl.candidate.json"),
            _candidate(),
        ),
        "stage_release": lambda *_args: _release(),
        "verify_release": lambda *_args: _release(),
        "runtime_identity": _identity,
        "activate_release": lambda *_args: ActivationResult(RELEASE_ID, BASE_ID),
        "rollback_release": lambda *_args: ActivationResult(RELEASE_ID, BASE_ID),
        "collision_errors": (_CollisionFailure,),
        "operation_errors": (_OperationFailure,),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.parametrize(
    ("argv", "action"),
    [
        (
            [
                "runtime",
                "lock",
                "--raw-freeze",
                "freeze.txt",
                "--approved-requirements",
                "approved.txt",
                "--wheelhouse",
                "wheelhouse",
                "--output",
                "runtime.lock",
            ],
            "lock",
        ),
        (
            [
                "runtime",
                "base",
                "--lock",
                "runtime.lock",
                "--approved-requirements",
                "approved.txt",
                "--wheelhouse",
                "wheelhouse",
            ],
            "base",
        ),
        (["runtime", "build", "--repo", "repo", "--out-dir", "out"], "build"),
        (
            [
                "runtime",
                "stage",
                "--candidate",
                "candidate.json",
                "--wheel",
                "app.whl",
                "--base-id",
                BASE_ID,
            ],
            "stage",
        ),
        (["runtime", "verify", RELEASE_ID], "verify"),
        (["runtime", "status", "--json"], "status"),
        (["runtime", "activate", RELEASE_ID], "activate"),
        (["runtime", "rollback"], "rollback"),
        (
            [
                "runtime",
                "promote",
                "--target-release",
                RELEASE_ID,
                "--rollback-anchor",
                ROLLBACK_RELEASE_ID,
                "--service-user",
                "worker",
                "--runtime-root",
                "/runtime",
            ],
            "promote",
        ),
        (
            ["runtime", "access-repair-current", "--service-user", "worker"],
            "access-repair-current",
        ),
        (
            ["runtime", "publish-staged-access", RELEASE_ID, "--service-user", "worker"],
            "publish-staged-access",
        ),
        (
            ["runtime", "bootstrap-managed-config", "--service-user", "worker"],
            "bootstrap-managed-config",
        ),
    ],
)
def test_runtime_parser_covers_every_action(argv: list[str], action: str) -> None:
    args = _parser().parse_args(argv)

    assert args.cmd == "runtime"
    assert args.runtime_action == action
    assert callable(args.func)


def test_runtime_root_can_appear_before_or_after_action() -> None:
    parser = _parser()

    before = parser.parse_args(["runtime", "--runtime-root", "before", "verify", RELEASE_ID])
    after = parser.parse_args(["runtime", "verify", RELEASE_ID, "--runtime-root", "after"])

    assert before.runtime_root == Path("before")
    assert after.runtime_root == Path("after")


def test_activate_with_rollback_anchor_only_delegates_to_explicit_port(monkeypatch, capsys) -> None:
    calls: list[tuple[Path, str, str]] = []

    def standard_activate(*_args):
        raise AssertionError("显式回滚锚点不得走标准激活端口")

    def anchored_activate(root: Path, target: str, rollback: str) -> ActivationResult:
        calls.append((root, target, rollback))
        return ActivationResult(target, rollback)

    monkeypatch.setattr(
        runtime_cli,
        "_default_ports",
        lambda: _ports(
            activate_release=standard_activate,
            activate_release_with_rollback_anchor=anchored_activate,
        ),
    )
    args = _parser().parse_args(
        [
            "runtime",
            "activate",
            RELEASE_ID,
            "--rollback-anchor",
            ROLLBACK_RELEASE_ID,
            "--runtime-root",
            "/explicit/runtime",
        ]
    )

    assert args.func(args) == 0
    assert calls == [(Path("/explicit/runtime"), RELEASE_ID, ROLLBACK_RELEASE_ID)]
    assert json.loads(capsys.readouterr().out)["result"] == {
        "active_release": RELEASE_ID,
        "previous_release": ROLLBACK_RELEASE_ID,
    }


def test_abbreviated_rollback_anchor_fails_before_runtime_root_resolution(
    monkeypatch, capsys
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        runtime_cli,
        "_default_ports",
        lambda: _ports(
            release_root=lambda *_args: (calls.append("runtime_root"), Path("/runtime"))[1],
            activate_release=lambda *_args: calls.append("activate"),
            activate_release_with_rollback_anchor=lambda *_args: calls.append("anchor"),
        ),
    )
    args = _parser().parse_args(["runtime", "activate", RELEASE_ID, "--rollback-anchor", "abc123"])

    assert args.func(args) == 1
    assert calls == []
    payload = json.loads(capsys.readouterr().err)
    assert payload == {
        "error": "runtime_id_invalid",
        "kind": "release",
        "object_id": RELEASE_ID,
        "status": "error",
    }
    assert "abc123" not in json.dumps(payload)


def test_explicit_runtime_root_has_priority_over_config(monkeypatch, capsys) -> None:
    observed: list[tuple[dict[str, object], Path | None]] = []

    def resolve(cfg, explicit=None):
        observed.append((cfg, explicit))
        return Path("/explicit/runtime")

    monkeypatch.setattr(runtime_cli, "_default_ports", lambda: _ports(release_root=resolve))
    args = _parser().parse_args(
        ["runtime", "verify", RELEASE_ID, "--runtime-root", "/explicit/runtime"]
    )

    assert args.func(args) == 0
    assert observed == [
        ({"runtime": {"release_root": "/configured/runtime"}}, Path("/explicit/runtime"))
    ]
    assert json.loads(capsys.readouterr().out)["result"]["release_id"] == RELEASE_ID


def test_configured_runtime_root_is_used_without_explicit_value(monkeypatch, capsys) -> None:
    observed: list[Path | None] = []

    def resolve(_cfg, explicit=None):
        observed.append(explicit)
        return Path("/configured/runtime")

    monkeypatch.setattr(runtime_cli, "_default_ports", lambda: _ports(release_root=resolve))
    args = _parser().parse_args(["runtime", "verify", RELEASE_ID])

    assert args.func(args) == 0
    assert observed == [None]


def test_managed_runtime_action_never_falls_back_to_process_home(monkeypatch, capsys) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        runtime_cli,
        "_default_ports",
        lambda: _ports(
            load_config=lambda: {"runtime": {"release_root": None}},
            release_root=lambda *_args: (called.append("resolved"), Path("/root/fallback"))[1],
            build_base=lambda *_args: (called.append("built"), _base())[1],
        ),
    )
    args = _parser().parse_args(
        [
            "runtime",
            "base",
            "--lock",
            "lock",
            "--approved-requirements",
            "approved",
            "--wheelhouse",
            "wheelhouse",
        ]
    )

    assert args.func(args) == 1
    assert called == []
    assert json.loads(capsys.readouterr().err) == {
        "error": "runtime_root_required",
        "kind": "base",
        "status": "error",
    }
    capsys.readouterr()


def test_lock_requires_persistent_wheelhouse() -> None:
    with pytest.raises(SystemExit) as error:
        _parser().parse_args(
            [
                "runtime",
                "lock",
                "--raw-freeze",
                "freeze.txt",
                "--approved-requirements",
                "approved.txt",
                "--output",
                "runtime.lock",
            ]
        )

    assert error.value.code == 2


def test_lock_can_preserve_explicit_wheelhouse(monkeypatch, capsys) -> None:
    observed: list[Path] = []

    def build(_freeze, _approved, _output, download_dir):
        observed.append(download_dir)
        return _lock_info()

    monkeypatch.setattr(
        runtime_cli,
        "_default_ports",
        lambda: _ports(build_hashed_lock=build),
    )
    args = _parser().parse_args(
        [
            "runtime",
            "lock",
            "--raw-freeze",
            "freeze.txt",
            "--approved-requirements",
            "approved.txt",
            "--output",
            "runtime.lock",
            "--wheelhouse",
            "wheelhouse",
        ]
    )

    assert args.func(args) == 0
    assert observed == [Path("wheelhouse")]
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


@pytest.mark.parametrize(
    ("argv", "expected_call"),
    [
        (
            [
                "runtime",
                "base",
                "--lock",
                "lock",
                "--approved-requirements",
                "approved",
                "--wheelhouse",
                "wheelhouse",
            ],
            "base",
        ),
        (["runtime", "build", "--repo", "repo", "--out-dir", "out"], "build"),
        (
            [
                "runtime",
                "stage",
                "--candidate",
                "candidate",
                "--wheel",
                "wheel",
                "--base-id",
                BASE_ID,
            ],
            "stage",
        ),
        (["runtime", "verify", RELEASE_ID], "verify"),
        (["runtime", "activate", RELEASE_ID], "activate"),
        (["runtime", "rollback"], "rollback"),
    ],
)
def test_runtime_action_delegates_to_public_port(
    argv: list[str], expected_call: str, monkeypatch, capsys
) -> None:
    called: list[str] = []
    ports = _ports(
        build_base=lambda *_args: (called.append("base"), _base())[1],
        build_candidate=lambda *_args: (
            called.append("build"),
            (Path("wheel"), Path("candidate"), _candidate()),
        )[1],
        stage_release=lambda *_args: (called.append("stage"), _release())[1],
        verify_release=lambda *_args: (called.append("verify"), _release())[1],
        activate_release=lambda *_args: (
            called.append("activate"),
            ActivationResult(RELEASE_ID, BASE_ID),
        )[1],
        rollback_release=lambda *_args: (
            called.append("rollback"),
            ActivationResult(RELEASE_ID, BASE_ID),
        )[1],
    )
    monkeypatch.setattr(runtime_cli, "_default_ports", lambda: ports)

    assert _parser().parse_args(argv).func(_parser().parse_args(argv)) == 0
    assert called == [expected_call]
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


@pytest.mark.parametrize(
    "argv",
    [
        [
            "runtime",
            "stage",
            "--candidate",
            "candidate",
            "--wheel",
            "wheel",
            "--base-id",
            "abc123",
        ],
        ["runtime", "verify", "abc123"],
        ["runtime", "activate", "abc123"],
    ],
)
def test_abbreviated_ids_fail_before_runtime_operation(argv, monkeypatch, capsys) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        runtime_cli,
        "_default_ports",
        lambda: _ports(
            stage_release=lambda *_args: called.append("stage"),
            verify_release=lambda *_args: called.append("verify"),
            activate_release=lambda *_args: called.append("activate"),
        ),
    )

    args = _parser().parse_args(argv)
    assert args.func(args) == 1
    assert called == []
    payload = json.loads(capsys.readouterr().err)
    assert payload == {
        "error": "runtime_id_invalid",
        "kind": "base" if args.runtime_action == "stage" else "release",
        "status": "error",
    }
    assert "abc123" not in json.dumps(payload)


def test_collision_error_is_fixed_and_keeps_only_full_object_id(monkeypatch, capsys) -> None:
    def collide(_root, _release_id):
        raise _CollisionFailure("不得泄露的碰撞输入 /secret token=abc")

    monkeypatch.setattr(
        runtime_cli,
        "_default_ports",
        lambda: _ports(verify_release=collide),
    )
    args = _parser().parse_args(["runtime", "verify", RELEASE_ID])

    assert args.func(args) == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload == {
        "error": "runtime_id_collision",
        "kind": "release",
        "object_id": RELEASE_ID,
        "status": "error",
    }
    assert "secret" not in json.dumps(payload)


@pytest.mark.parametrize(
    ("argv", "port_name", "kind"),
    [
        (
            ["runtime", "build", "--repo", "repo", "--out-dir", "out"],
            "build_candidate",
            "candidate",
        ),
        (
            [
                "runtime",
                "stage",
                "--candidate",
                "candidate",
                "--wheel",
                "wheel",
                "--base-id",
                BASE_ID,
            ],
            "stage_release",
            "release",
        ),
    ],
)
def test_candidate_and_release_collision_use_shared_fixed_code(
    argv, port_name, kind, monkeypatch, capsys
) -> None:
    def collide(*_args):
        raise RuntimeIdCollisionError("不得泄露的碰撞细节 /secret")

    monkeypatch.setattr(
        runtime_cli,
        "_default_ports",
        lambda: _ports(
            **{
                port_name: collide,
                "collision_errors": (RuntimeIdCollisionError,),
            }
        ),
    )
    args = _parser().parse_args(argv)

    assert args.func(args) == 1
    assert json.loads(capsys.readouterr().err) == {
        "error": "runtime_id_collision",
        "kind": kind,
        "status": "error",
    }


def test_operation_error_does_not_expose_exception_or_paths(monkeypatch, capsys) -> None:
    def fail(_repo, _out_dir):
        raise _OperationFailure("token=abc; /secret/build; pip install --index-url credential")

    monkeypatch.setattr(runtime_cli, "_default_ports", lambda: _ports(build_candidate=fail))
    args = _parser().parse_args(
        ["runtime", "build", "--repo", "/secret/repo", "--out-dir", "/secret/out"]
    )

    assert args.func(args) == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload == {
        "error": "runtime_build_failed",
        "kind": "candidate",
        "status": "error",
    }
    assert "secret" not in json.dumps(payload)
    assert "token" not in json.dumps(payload)


def test_default_error_boundary_covers_malformed_configuration() -> None:
    ports = runtime_cli._default_ports()

    assert ValueError in ports.operation_errors
    assert TypeError in ports.operation_errors


def test_status_uses_verified_runtime_identity_and_redacts_local_paths(monkeypatch, capsys) -> None:
    monkeypatch.setattr(runtime_cli, "_default_ports", lambda: _ports())
    args = _parser().parse_args(["runtime", "status", "--json"])

    assert args.func(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"] == {
        "base_id": BASE_ID,
        "base_requirements_sha256": REQUIREMENTS_SHA,
        "mode": "release",
        "release_id": RELEASE_ID,
        "runtime_revision": REVISION,
        "wheel_sha256": WHEEL_SHA,
    }
    serialized = json.dumps(payload)
    assert "interpreter_realpath" not in serialized
    assert "environment_prefix" not in serialized
    assert "source_root" not in serialized
    assert "/secret/" not in serialized


def test_status_rejects_non_release_identity(monkeypatch, capsys) -> None:
    installed = RuntimeIdentity(
        mode="installed",
        runtime_revision="8" * 64,
        release_id=None,
        wheel_sha256=None,
        base_id=None,
        base_requirements_sha256=None,
        interpreter_realpath="/secret/python",
        environment_prefix="/secret/prefix",
        source_root=None,
    )
    monkeypatch.setattr(
        runtime_cli,
        "_default_ports",
        lambda: _ports(runtime_identity=lambda: installed),
    )
    args = _parser().parse_args(["runtime", "status", "--json"])

    assert args.func(args) == 1
    assert json.loads(capsys.readouterr().err) == {
        "error": "runtime_status_unavailable",
        "kind": "runtime",
        "status": "error",
    }


def test_default_config_has_versioned_release_root() -> None:
    from codev_platform.core.config import DEFAULTS

    assert DEFAULTS["runtime"]["release_root"] is None
