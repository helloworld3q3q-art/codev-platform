"""systemd 目标用户瞬时预检适配器测试。"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import codev_platform.mcp_systemd_target_preflight as target_preflight
from codev_platform.mcp_systemd_install_contract import (
    SystemdInstallManifest,
    SystemdInstallTransactionError,
    SystemdRuntimeBinding,
    SystemdUnitActivationMode,
    SystemdUnitInstallSpec,
    SystemdUnitPayload,
)
from codev_platform.mcp_systemd_unit_registry import MANAGED_SYSTEMD_UNITS


_ROOT = "/var/lib/codev-platform/runtime"
_RELEASE_ID = "a" * 64
_REVISION = "b" * 40
_USER = "helloworld"
_REAL_TARGET_USER_WORKING_DIRECTORY = target_preflight._target_user_working_directory


@pytest.fixture(autouse=True)
def _fixed_target_user_working_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        target_preflight,
        "_target_user_working_directory",
        lambda _user: "/home/helloworld",
    )


def _install_input(tmp_path: Path, *, environment_file: str | None = None):
    payloads: list[SystemdUnitPayload] = []
    specs: list[SystemdUnitInstallSpec] = []
    for item in MANAGED_SYSTEMD_UNITS.values():
        content = f"[Unit]\nDescription={item.name}\n".encode()
        spec = SystemdUnitInstallSpec(
            source=(tmp_path / item.name).resolve(),
            content_digest=hashlib.sha256(content).hexdigest(),
            enable=item.enable,
            restart=item.restart,
            activation_mode=SystemdUnitActivationMode(item.activation_mode),
        )
        specs.append(spec)
        payloads.append(SystemdUnitPayload(spec=spec, content=content))
    binding = SystemdRuntimeBinding(
        release_root=_ROOT,
        expected_release_id=_RELEASE_ID,
        target_user=_USER,
        environment_file=environment_file,
    )
    manifest = SystemdInstallManifest(
        units=tuple(specs),
        runtime_revision=_REVISION,
        runtime_binding=binding,
    )
    bound = SimpleNamespace(
        root=Path(_ROOT),
        release_id=_RELEASE_ID,
        runtime_revision=_REVISION,
        interpreter_path=Path(binding.immutable_python.as_posix()),
    )
    return manifest, tuple(payloads), bound


def _proof(manifest: SystemdInstallManifest, payloads: tuple[SystemdUnitPayload, ...]):
    binding = manifest.runtime_binding
    assert binding is not None
    return {
        "release_id": binding.expected_release_id,
        "runtime_revision": manifest.runtime_revision,
        "schema_version": 1,
        "target_user": binding.target_user,
        "units": [
            {"name": payload.spec.unit_name, "sha256": payload.spec.content_digest}
            for payload in sorted(payloads, key=lambda item: item.spec.unit_name)
        ],
    }


def _immutable_protected_payloads(
    manifest: SystemdInstallManifest,
    payloads: tuple[SystemdUnitPayload, ...],
) -> tuple[SystemdUnitPayload, ...]:
    binding = manifest.runtime_binding
    assert binding is not None
    protected = {"codev-mcp-codegraph.service", "codev-reindex.service"}
    immutable = binding.immutable_python.as_posix().encode("ascii")
    result: list[SystemdUnitPayload] = []
    for payload in payloads:
        content = payload.content
        if payload.spec.unit_name in protected:
            content = b"[Service]\nExecStart=" + immutable + b" -I -m codev_platform.cli\n"
        result.append(
            SystemdUnitPayload(
                spec=replace(
                    payload.spec,
                    content_digest=hashlib.sha256(content).hexdigest(),
                ),
                content=content,
            )
        )
    return tuple(result)


def _legacy_current_alias_proof(
    manifest: SystemdInstallManifest,
    payloads: tuple[SystemdUnitPayload, ...],
) -> dict[str, object]:
    binding = manifest.runtime_binding
    assert binding is not None
    protected = {"codev-mcp-codegraph.service", "codev-reindex.service"}
    immutable = binding.immutable_python.as_posix().encode("ascii")
    current = f"{binding.release_root}/current/venv/bin/python".encode("ascii")
    transformed: list[SystemdUnitPayload] = []
    for payload in payloads:
        content = payload.content
        if payload.spec.unit_name in protected:
            assert content.count(immutable) == 1
            content = content.replace(immutable, current)
        transformed.append(
            SystemdUnitPayload(
                spec=replace(
                    payload.spec,
                    content_digest=hashlib.sha256(content).hexdigest(),
                ),
                content=content,
            )
        )
    return _proof(manifest, tuple(transformed))


def _显式工作目录与不可变解释器载荷(
    manifest: SystemdInstallManifest,
    payloads: tuple[SystemdUnitPayload, ...],
) -> tuple[SystemdUnitPayload, ...]:
    """构造新 controller 的生产载荷，用于冻结 release 的兼容证明回归。"""
    binding = manifest.runtime_binding
    assert binding is not None
    protected = {"codev-mcp-codegraph.service", "codev-reindex.service"}
    immutable = binding.immutable_python.as_posix().encode("ascii")
    result: list[SystemdUnitPayload] = []
    for payload in payloads:
        name = payload.spec.unit_name
        registration = MANAGED_SYSTEMD_UNITS[name]
        content = b"[Unit]\nDescription=" + name.encode("ascii") + b"\n"
        if registration.runtime_bound:
            content += b"[Service]\nWorkingDirectory=/home/helloworld\n"
        if name in protected:
            content += b"ExecStart=" + immutable + b" -I -m codev_platform.cli\n"
        result.append(
            SystemdUnitPayload(
                spec=replace(
                    payload.spec,
                    content_digest=hashlib.sha256(content).hexdigest(),
                ),
                content=content,
            )
        )
    return tuple(result)


def _冻结release联合旧语义proof(
    manifest: SystemdInstallManifest,
    payloads: tuple[SystemdUnitPayload, ...],
) -> dict[str, object]:
    """模拟旧 runtime 同时渲染 %h 与 current alias 的唯一摘要。"""
    binding = manifest.runtime_binding
    assert binding is not None
    protected = {"codev-mcp-codegraph.service", "codev-reindex.service"}
    immutable = binding.immutable_python.as_posix().encode("ascii")
    current = f"{binding.release_root}/current/venv/bin/python".encode("ascii")
    explicit_home = b"WorkingDirectory=/home/helloworld\n"
    result: list[SystemdUnitPayload] = []
    for payload in payloads:
        name = payload.spec.unit_name
        content = payload.content
        if MANAGED_SYSTEMD_UNITS[name].runtime_bound:
            assert content.count(explicit_home) == 1
            content = content.replace(explicit_home, b"WorkingDirectory=%h\n")
        if name in protected:
            assert content.count(immutable) == 1
            content = content.replace(immutable, current)
        result.append(
            SystemdUnitPayload(
                spec=replace(
                    payload.spec,
                    content_digest=hashlib.sha256(content).hexdigest(),
                ),
                content=content,
            )
        )
    return _proof(manifest, tuple(result))


def _冻结release联合旧语义proof_lax(
    manifest: SystemdInstallManifest,
    payloads: tuple[SystemdUnitPayload, ...],
) -> dict[str, object]:
    """仅供拒绝回归构造不安全摘要；生产兼容逻辑不得采用这个宽松替换。"""
    binding = manifest.runtime_binding
    assert binding is not None
    protected = {"codev-mcp-codegraph.service", "codev-reindex.service"}
    immutable = binding.immutable_python.as_posix().encode("ascii")
    current = f"{binding.release_root}/current/venv/bin/python".encode("ascii")
    result: list[SystemdUnitPayload] = []
    for payload in payloads:
        content = payload.content.replace(
            b"WorkingDirectory=/home/helloworld\n",
            b"WorkingDirectory=%h\n",
        )
        if payload.spec.unit_name in protected:
            content = content.replace(immutable, current)
        result.append(
            SystemdUnitPayload(
                spec=replace(
                    payload.spec,
                    content_digest=hashlib.sha256(content).hexdigest(),
                ),
                content=content,
            )
        )
    return _proof(manifest, tuple(result))


def test_瞬时证明使用不可变解释器同源环境双层超时并逐项核对摘要(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest, payloads, bound = _install_input(
        tmp_path,
        environment_file="/etc/codev-platform/service.env",
    )
    expected = _proof(manifest, payloads)
    calls: list[tuple[tuple[str, ...], float]] = []

    def execute(command: tuple[str, ...], timeout_sec: float) -> bytes:
        calls.append((command, timeout_sec))
        return (json.dumps(expected, sort_keys=True, separators=(",", ":")) + "\n").encode()

    monkeypatch.setattr(target_preflight, "_execute_transient", execute)

    target_preflight.verify_target_user_systemd_preflight(manifest, payloads, bound)

    assert len(calls) == 1
    command, timeout_sec = calls[0]
    assert command[:6] == (
        "/usr/bin/systemd-run",
        "--quiet",
        "--wait",
        "--pipe",
        "--collect",
        f"--uid={_USER}",
    )
    assert "--property=RuntimeMaxSec=30s" in command
    assert "--property=EnvironmentFile=/etc/codev-platform/service.env" in command
    assert "--property=WorkingDirectory=/home/helloworld" in command
    assert "--property=WorkingDirectory=%h" not in command
    assert f"--setenv=CODEV_PLATFORM_RELEASE_FILE={_ROOT}/current/release.json" in command
    assert command[-12:] == (
        f"{_ROOT}/releases/{_RELEASE_ID}/venv/bin/python",
        "-I",
        "-m",
        "codev_platform.runtime_preflight_proof",
        "--release-root",
        _ROOT,
        "--expected-release-id",
        _RELEASE_ID,
        "--expected-runtime-revision",
        _REVISION,
        "--target-user",
        _USER,
    )
    assert timeout_sec == 40.0


def test_旧release的受保护current别名proof仅按固定映射兼容(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest, payloads, bound = _install_input(tmp_path)
    immutable_payloads = _immutable_protected_payloads(manifest, payloads)
    legacy_proof = _legacy_current_alias_proof(manifest, immutable_payloads)
    assert legacy_proof != _proof(manifest, immutable_payloads)
    monkeypatch.setattr(
        target_preflight,
        "_execute_transient",
        lambda *_args: (
            json.dumps(legacy_proof, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode(),
    )

    target_preflight.verify_target_user_systemd_preflight(
        manifest,
        immutable_payloads,
        bound,
    )


def test_冻结release联合旧语义proof仅按固定映射兼容(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest, payloads, bound = _install_input(tmp_path)
    current_payloads = _显式工作目录与不可变解释器载荷(manifest, payloads)
    legacy_proof = _冻结release联合旧语义proof(manifest, current_payloads)
    assert legacy_proof != _proof(manifest, current_payloads)
    monkeypatch.setattr(
        target_preflight,
        "_execute_transient",
        lambda *_args: (
            json.dumps(legacy_proof, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode(),
    )

    target_preflight.verify_target_user_systemd_preflight(
        manifest,
        current_payloads,
        bound,
    )


def test_冻结release联合旧语义proof仍逐项拒绝普通unit摘要漂移(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest, payloads, bound = _install_input(tmp_path)
    current_payloads = _显式工作目录与不可变解释器载荷(manifest, payloads)
    legacy_proof = _冻结release联合旧语义proof(manifest, current_payloads)
    units = legacy_proof["units"]
    assert type(units) is list
    drifted = [dict(item) for item in units]
    normal = next(item for item in drifted if item["name"] == "codev-agent.service")
    normal["sha256"] = "f" * 64
    legacy_proof["units"] = drifted
    monkeypatch.setattr(
        target_preflight,
        "_execute_transient",
        lambda *_args: (
            json.dumps(legacy_proof, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode(),
    )

    with pytest.raises(SystemdInstallTransactionError, match="预检证明不一致"):
        target_preflight.verify_target_user_systemd_preflight(
            manifest,
            current_payloads,
            bound,
        )


def test_冻结release联合旧语义proof要求每个运行时unit仅有一处显式工作目录(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest, payloads, bound = _install_input(tmp_path)
    current_payloads = list(_显式工作目录与不可变解释器载荷(manifest, payloads))
    index = next(
        index
        for index, payload in enumerate(current_payloads)
        if payload.spec.unit_name == "codev-agent.service"
    )
    original = current_payloads[index]
    content = original.content + b"WorkingDirectory=/home/helloworld\n"
    current_payloads[index] = SystemdUnitPayload(
        spec=replace(
            original.spec,
            content_digest=hashlib.sha256(content).hexdigest(),
        ),
        content=content,
    )
    legacy_proof = _冻结release联合旧语义proof_lax(manifest, tuple(current_payloads))
    monkeypatch.setattr(
        target_preflight,
        "_execute_transient",
        lambda *_args: (
            json.dumps(legacy_proof, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode(),
    )

    with pytest.raises(SystemdInstallTransactionError, match="预检证明不一致"):
        target_preflight.verify_target_user_systemd_preflight(
            manifest,
            tuple(current_payloads),
            bound,
        )


def test_冻结release联合旧语义proof拒绝非运行时unit的显式工作目录(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest, payloads, bound = _install_input(tmp_path)
    current_payloads = list(_显式工作目录与不可变解释器载荷(manifest, payloads))
    index = next(
        index
        for index, payload in enumerate(current_payloads)
        if payload.spec.unit_name == "codev-clock-resync.service"
    )
    original = current_payloads[index]
    content = original.content + b"[Service]\nWorkingDirectory=/home/helloworld\n"
    current_payloads[index] = SystemdUnitPayload(
        spec=replace(
            original.spec,
            content_digest=hashlib.sha256(content).hexdigest(),
        ),
        content=content,
    )
    legacy_proof = _冻结release联合旧语义proof_lax(manifest, tuple(current_payloads))
    monkeypatch.setattr(
        target_preflight,
        "_execute_transient",
        lambda *_args: (
            json.dumps(legacy_proof, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode(),
    )

    with pytest.raises(SystemdInstallTransactionError, match="预检证明不一致"):
        target_preflight.verify_target_user_systemd_preflight(
            manifest,
            tuple(current_payloads),
            bound,
        )


def test_旧release兼容分支仍逐项拒绝普通unit摘要漂移(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest, payloads, bound = _install_input(tmp_path)
    immutable_payloads = _immutable_protected_payloads(manifest, payloads)
    legacy_proof = _legacy_current_alias_proof(manifest, immutable_payloads)
    units = legacy_proof["units"]
    assert type(units) is list
    drifted = [dict(item) for item in units]
    normal = next(item for item in drifted if item["name"] == "codev-agent.service")
    normal["sha256"] = "f" * 64
    legacy_proof["units"] = drifted
    monkeypatch.setattr(
        target_preflight,
        "_execute_transient",
        lambda *_args: (
            json.dumps(legacy_proof, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode(),
    )

    with pytest.raises(SystemdInstallTransactionError, match="预检证明不一致"):
        target_preflight.verify_target_user_systemd_preflight(
            manifest,
            immutable_payloads,
            bound,
        )


def test_旧release兼容分支要求受保护解释器字节串恰好一次(
    tmp_path: Path,
) -> None:
    manifest, payloads, _bound = _install_input(tmp_path)
    immutable_payloads = list(_immutable_protected_payloads(manifest, payloads))
    binding = manifest.runtime_binding
    assert binding is not None
    index = next(
        index
        for index, payload in enumerate(immutable_payloads)
        if payload.spec.unit_name == "codev-reindex.service"
    )
    original = immutable_payloads[index]
    content = original.content + binding.immutable_python.as_posix().encode("ascii")
    immutable_payloads[index] = SystemdUnitPayload(
        spec=replace(
            original.spec,
            content_digest=hashlib.sha256(content).hexdigest(),
        ),
        content=content,
    )

    with pytest.raises(SystemdInstallTransactionError, match="预检证明不一致"):
        target_preflight._legacy_current_alias_proof(
            manifest,
            tuple(immutable_payloads),
            binding,
        )


def test_旧release兼容分支要求不可变解释器位于受保护ExecStart(
    tmp_path: Path,
) -> None:
    manifest, payloads, _bound = _install_input(tmp_path)
    immutable_payloads = list(_immutable_protected_payloads(manifest, payloads))
    binding = manifest.runtime_binding
    assert binding is not None
    index = next(
        index
        for index, payload in enumerate(immutable_payloads)
        if payload.spec.unit_name == "codev-reindex.service"
    )
    original = immutable_payloads[index]
    content = (
        b"[Service]\nDescription="
        + binding.immutable_python.as_posix().encode("ascii")
        + b"\nExecStart="
        + f"{binding.release_root}/current/venv/bin/python".encode("ascii")
        + b" -I -m codev_platform.cli\n"
    )
    immutable_payloads[index] = SystemdUnitPayload(
        spec=replace(
            original.spec,
            content_digest=hashlib.sha256(content).hexdigest(),
        ),
        content=content,
    )

    with pytest.raises(SystemdInstallTransactionError, match="预检证明不一致"):
        target_preflight._legacy_current_alias_proof(
            manifest,
            tuple(immutable_payloads),
            binding,
        )


def test_目标用户主目录无法解析时不得启动瞬时服务(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest, payloads, bound = _install_input(tmp_path)

    monkeypatch.setattr(
        target_preflight,
        "_target_user_working_directory",
        _REAL_TARGET_USER_WORKING_DIRECTORY,
    )
    monkeypatch.setattr(
        target_preflight,
        "resolve_service_account",
        lambda _user: (_ for _ in ()).throw(RuntimeError("账号不可用")),
    )
    monkeypatch.setattr(
        target_preflight,
        "_execute_transient",
        lambda *_args: pytest.fail("主目录无法证明时不得启动 systemd-run"),
    )

    with pytest.raises(SystemdInstallTransactionError, match="目标用户主目录无效"):
        target_preflight.verify_target_user_systemd_preflight(manifest, payloads, bound)


def test_目标用户主目录复用既有服务账号真值(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        target_preflight,
        "_target_user_working_directory",
        _REAL_TARGET_USER_WORKING_DIRECTORY,
    )
    monkeypatch.setattr(
        target_preflight,
        "resolve_service_account",
        lambda user: SimpleNamespace(name=user, home=Path("/srv/codev-worker")),
    )

    assert target_preflight._target_user_working_directory(_USER) == "/srv/codev-worker"


def test_证明摘要不一致时在适配层失败关闭(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest, payloads, bound = _install_input(tmp_path)
    result = _proof(manifest, payloads)
    result["units"][0]["sha256"] = "f" * 64
    monkeypatch.setattr(
        target_preflight,
        "_execute_transient",
        lambda *_args: (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode(),
    )

    with pytest.raises(SystemdInstallTransactionError, match="目标用户预检证明不一致"):
        target_preflight.verify_target_user_systemd_preflight(manifest, payloads, bound)


@pytest.mark.parametrize(
    "case",
    ("noise", "duplicate", "nan", "oversized"),
)
def test_证明协议拒绝噪声重复键非有限值与超长输出(
    case: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest, payloads, bound = _install_input(tmp_path)
    output = {
        "noise": b"not-json",
        "duplicate": b'{"schema_version":1,"schema_version":1}',
        "nan": b'{"schema_version":NaN}',
        "oversized": b"{}" + b"x" * (64 * 1024),
    }[case]
    monkeypatch.setattr(target_preflight, "_execute_transient", lambda *_args: output)

    with pytest.raises(SystemdInstallTransactionError, match="输出无效"):
        target_preflight.verify_target_user_systemd_preflight(manifest, payloads, bound)


def test_瞬时进程失败与超时不泄漏子进程输出(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[dict[str, object]] = []

    def failed_run(*args, **kwargs):
        commands.append({"args": args, "kwargs": kwargs})
        return subprocess.CompletedProcess(args[0], 7, stdout=b"private-output")

    monkeypatch.setattr(target_preflight.subprocess, "run", failed_run)
    with pytest.raises(SystemdInstallTransactionError) as failed:
        target_preflight._execute_transient(("safe-command",), 4.0)
    assert "private-output" not in str(failed.value)
    assert commands[0]["kwargs"] == {
        "check": False,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.DEVNULL,
        "timeout": 4.0,
    }

    monkeypatch.setattr(
        target_preflight.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("safe-command", 4.0, output=b"private-timeout")
        ),
    )
    with pytest.raises(SystemdInstallTransactionError) as timed_out:
        target_preflight._execute_transient(("safe-command",), 4.0)
    assert "private-timeout" not in str(timed_out.value)


def test_锁内绑定漂移时不得启动瞬时进程(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest, payloads, bound = _install_input(tmp_path)
    bound.release_id = "c" * 64
    monkeypatch.setattr(
        target_preflight,
        "_execute_transient",
        lambda *_args: pytest.fail("绑定漂移后不得启动 systemd-run"),
    )

    with pytest.raises(SystemdInstallTransactionError, match="运行时绑定不一致"):
        target_preflight.verify_target_user_systemd_preflight(manifest, payloads, bound)
