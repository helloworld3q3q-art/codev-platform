"""依赖基座深验证、静态复验与执行隔离测试。"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform.core.runtime_models import compute_base_id
from codev_platform.runtime_distribution_inventory import DistributionInventoryProof
from tests.test_runtime_base import _abi, _install_fake_ports, _lock_info


def _proof(digest: str = "e") -> DistributionInventoryProof:
    return DistributionInventoryProof(
        inventory_sha256=digest * 64,
        distributions=("demo==1.0",),
        file_count=1,
        total_bytes=1,
    )


def test_build_static_inventory_wraps_the_only_deep_probe(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from codev_platform import runtime_base

    lock = tmp_path / "wsl-runtime.lock"
    lock.write_bytes(b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    info = _lock_info(lock.read_bytes())
    events: list[object] = []
    _install_fake_ports(monkeypatch, runtime_base, info, events=events)

    runtime_base.build_base(tmp_path / "runtime", lock, tmp_path / "approved.txt")

    inventory_positions = [
        index
        for index, event in enumerate(events)
        if isinstance(event, tuple) and event[0] == "inventory"
    ]
    deep_positions = [
        index
        for index, event in enumerate(events)
        if isinstance(event, tuple)
        and event[0] == "python"
        and "install" not in event[2]
    ]
    assert len(deep_positions) == 3
    assert inventory_positions[0] < deep_positions[0]
    assert deep_positions[-1] < inventory_positions[1]


def test_static_verify_never_executes_target_python(tmp_path: Path, monkeypatch) -> None:
    from codev_platform import runtime_base

    lock = tmp_path / "wsl-runtime.lock"
    lock.write_bytes(b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    info = _lock_info(lock.read_bytes())
    root = tmp_path / "runtime"
    _install_fake_ports(monkeypatch, runtime_base, info)
    built = runtime_base.build_base(root, lock, tmp_path / "approved.txt")
    _install_fake_ports(
        monkeypatch,
        runtime_base,
        info,
        run_python_override=lambda *_args: pytest.fail("静态验证不得执行目标 Python"),
    )

    assert runtime_base.verify_base(root, built.base_id) == built


def test_dynamic_probe_inventory_drift_fails_before_metadata_completion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from codev_platform import runtime_base

    lock = tmp_path / "wsl-runtime.lock"
    lock.write_bytes(b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    info = _lock_info(lock.read_bytes())
    proofs = iter((_proof("e"), _proof("f")))
    _install_fake_ports(
        monkeypatch,
        runtime_base,
        info,
        verify_inventory_override=lambda *_args: next(proofs),
    )

    with pytest.raises(runtime_base.RuntimeBaseIntegrityError, match="探针期间"):
        runtime_base.build_base(tmp_path / "runtime", lock, tmp_path / "approved.txt")

    bases = tuple((tmp_path / "runtime" / "bases").iterdir())
    assert len(bases) == 1
    assert (bases[0] / ".incomplete").read_text(encoding="ascii") == "after_install\n"


def test_static_inventory_digest_drift_fails_without_python(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from codev_platform import runtime_base

    lock = tmp_path / "wsl-runtime.lock"
    lock.write_bytes(b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    info = _lock_info(lock.read_bytes())
    root = tmp_path / "runtime"
    _install_fake_ports(monkeypatch, runtime_base, info)
    built = runtime_base.build_base(root, lock, tmp_path / "approved.txt")
    _install_fake_ports(
        monkeypatch,
        runtime_base,
        info,
        verify_inventory_override=lambda *_args: _proof("f"),
        run_python_override=lambda *_args: pytest.fail("清单漂移不得执行目标 Python"),
    )

    with pytest.raises(runtime_base.RuntimeBaseIntegrityError, match="静态清单漂移"):
        runtime_base.verify_base(root, built.base_id)


def test_python_runner_forces_isolation_and_disables_bytecode(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from codev_platform import runtime_base, runtime_base_environment

    observed: dict[str, object] = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["environment"] = kwargs["env"]
        return SimpleNamespace(stdout=b"")

    monkeypatch.setattr(runtime_base_environment.subprocess, "run", fake_run)
    monkeypatch.setenv("CODEV_PLATFORM_MEMORY_DSN", "不得传给构建探针")

    runtime_base._run_python(tmp_path / "python", ("-I", "-m", "pip", "check"))

    assert observed["command"] == (
        str(tmp_path / "python"),
        "-B",
        "-I",
        "-m",
        "pip",
        "check",
    )
    assert observed["environment"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert observed["environment"]["PYTHONPATH"] == ""
    assert observed["environment"]["PYTHONNOUSERSITE"] == "1"
    assert "CODEV_PLATFORM_MEMORY_DSN" not in observed["environment"]


@pytest.mark.parametrize(
    ("failure", "expected_stage"),
    [("venv", "after_marker"), ("install", "after_venv"), ("probe", "after_install")],
)
def test_build_failure_preserves_precise_incomplete_stage(
    tmp_path: Path,
    monkeypatch,
    failure: str,
    expected_stage: str,
) -> None:
    from codev_platform import runtime_base

    lock = tmp_path / "wsl-runtime.lock"
    lock.write_bytes(b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    info = _lock_info(lock.read_bytes())
    base_id = compute_base_id(info.requirements_sha256, _abi())

    def create(base_dir: Path):
        if failure == "venv":
            raise RuntimeError("注入 venv 失败")
        python = base_dir / "venv" / "bin" / "python"
        purelib = base_dir / "venv" / "lib" / "python3.11" / "site-packages"
        python.parent.mkdir(parents=True)
        python.write_bytes(b"python")
        purelib.mkdir(parents=True)
        return runtime_base._VenvLayout(python, purelib, python.parent)

    def run(_python: Path, arguments: tuple[str, ...]) -> bytes:
        if failure == "install" or (failure == "probe" and "check" in arguments):
            raise RuntimeError("注入 Python 失败")
        return b"demo==1.0\n" if "freeze" in arguments else b""

    _install_fake_ports(
        monkeypatch,
        runtime_base,
        info,
        create_venv_override=create,
        run_python_override=run,
    )

    with pytest.raises(RuntimeError, match="注入"):
        runtime_base.build_base(tmp_path / "runtime", lock, tmp_path / "approved.txt")

    marker = tmp_path / "runtime" / "bases" / base_id / ".incomplete"
    assert marker.read_text(encoding="ascii") == f"{expected_stage}\n"


@pytest.mark.parametrize("drift", ["lock", "abi", "freeze", "marker"])
def test_verify_rejects_persisted_base_drift(tmp_path: Path, monkeypatch, drift: str) -> None:
    from codev_platform import runtime_base

    lock = tmp_path / "wsl-runtime.lock"
    lock.write_bytes(b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    info = _lock_info(lock.read_bytes())
    root = tmp_path / "runtime"
    _install_fake_ports(monkeypatch, runtime_base, info)
    built = runtime_base.build_base(root, lock, tmp_path / "approved.txt")
    base_dir = root / "bases" / built.base_id
    options: dict[str, object] = {}
    if drift == "lock":
        (base_dir / "requirements.lock").write_bytes(b"drift\n")
    elif drift == "abi":
        options["runtime_abi"] = dataclasses.replace(_abi(), machine="aarch64")
    elif drift == "freeze":
        options["freeze"] = b"demo==2.0\n"
    else:
        (base_dir / ".incomplete").write_text("after_install\n", encoding="ascii")
    _install_fake_ports(monkeypatch, runtime_base, info, **options)

    verifier = runtime_base.verify_base_deep if drift == "freeze" else runtime_base.verify_base
    with pytest.raises(runtime_base.RuntimeBaseIntegrityError):
        verifier(root, built.base_id)


@pytest.mark.skipif(os.name != "posix", reason="POSIX owner/mode 语义")
def test_posix_trust_accepts_internal_dir_link_and_checks_bases(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from codev_platform import runtime_execution_trust as trust

    base = tmp_path / "runtime" / "bases" / ("a" * 64)
    (base / "lib").mkdir(parents=True)
    (base / "lib64").symlink_to("lib", target_is_directory=True)
    original_lstat = Path.lstat

    def root_owned(path: Path):
        values = list(original_lstat(path))
        values[4] = 0
        return os.stat_result(values)

    monkeypatch.setattr(Path, "lstat", root_owned)
    trust._require_posix_tree_trust(base)
    checked: list[Path] = []
    monkeypatch.setattr(trust, "_is_linklike", lambda _path: False)
    monkeypatch.setattr(
        trust,
        "_require_posix_entry",
        lambda path, *, directory: checked.append(path),
    )
    trust._require_posix_ancestor_trust(base)
    assert base.parent in checked


@pytest.mark.parametrize(
    "drift",
    [
        {"approved_index_url": "https://example.invalid/simple"},
        {"artifact_manifest_sha256": "c" * 64},
    ],
)
def test_verify_base_recomputes_index_and_artifact_manifest(
    tmp_path: Path,
    monkeypatch,
    drift: dict[str, str],
) -> None:
    from codev_platform import runtime_base

    lock = tmp_path / "wsl-runtime.lock"
    lock.write_bytes(b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    info = _lock_info(lock.read_bytes())
    _install_fake_ports(monkeypatch, runtime_base, info)
    root = tmp_path / "runtime"
    built = runtime_base.build_base(root, lock, tmp_path / "approved.txt")
    inspected = dataclasses.replace(info, **drift)
    _install_fake_ports(
        monkeypatch,
        runtime_base,
        info,
        inspected_lock_info=inspected,
    )

    with pytest.raises(runtime_base.RuntimeBaseIntegrityError, match="依赖锁"):
        runtime_base.verify_base(root, built.base_id)
