"""运行时身份来源优先级、完整性证明与进程缓存测试。"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
import subprocess
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform.core import runtime_identity as identity_module
from codev_platform.core.runtime_identity import RuntimeIdentityError
from codev_platform.core.runtime_models import (
    RUNTIME_ACCESS_PROFILE,
    BaseMetadata,
    ReleaseMetadata,
    RuntimeAbi,
    compute_base_id,
    compute_release_id,
    current_abi,
    sha256_file,
    write_base_metadata_atomic,
    write_release_metadata_atomic,
)
from tests.runtime_support import write_fake_release

_LOCK_BYTES = b"demo==1.0 --hash=sha256:abc\n"


class _Distribution:
    """为 PEP 610 与 RECORD 分支提供最小真实文件分布对象。"""

    def __init__(
        self,
        root: Path,
        *,
        direct_url: str | None,
        version: str = "1.2.3",
        records: int = 1,
        record_bytes: bytes = b"module.py,sha256=abc,1\n",
    ) -> None:
        self._direct_url = direct_url
        self.version = version
        self.files: list[Path] = []
        for index in range(records):
            relative = Path(f"codev_platform-{index}.dist-info") / "RECORD"
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(record_bytes + str(index).encode("ascii"))
            self.files.append(relative)
        self._root = root

    def read_text(self, name: str) -> str | None:
        assert name == "direct_url.json"
        return self._direct_url

    def locate_file(self, relative: Path) -> Path:
        return self._root / relative


@pytest.fixture(autouse=True)
def _clean_identity_cache(monkeypatch):
    identity_module.runtime_identity.cache_clear()
    monkeypatch.delenv("CODEV_PLATFORM_RELEASE_FILE", raising=False)
    yield
    identity_module.runtime_identity.cache_clear()


def _base_metadata(abi: RuntimeAbi | None = None) -> BaseMetadata:
    runtime_abi = abi or current_abi()
    requirements_sha = hashlib.sha256(_LOCK_BYTES).hexdigest()
    return BaseMetadata(
        schema_version=3,
        access_profile=RUNTIME_ACCESS_PROFILE,
        base_id=compute_base_id(requirements_sha, runtime_abi),
        requirements_sha256=requirements_sha,
        approved_index_url="https://download.pytorch.org/whl/cu128",
        artifact_manifest_sha256="b" * 64,
        freeze_sha256="c" * 64,
        purelib_inventory_sha256="d" * 64,
        abi=runtime_abi,
        created_at="2026-07-11T00:00:00Z",
        python_relative="venv/bin/python",
        purelib_relative="venv/lib/python/site-packages",
        bin_relative="venv/bin",
        lock_relative="requirements.lock",
    )


def _write_release_tree(
    root: Path,
    *,
    revision: str = "d" * 40,
    release_id_override: str | None = None,
) -> tuple[Path, ReleaseMetadata, BaseMetadata]:
    base = _base_metadata()
    base_dir = root / "bases" / base.base_id
    lock_path = base_dir / base.lock_relative
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_bytes(_LOCK_BYTES)
    write_base_metadata_atomic(base_dir / "base.json", base)
    base_metadata_sha = sha256_file(base_dir / "base.json")
    wheel_sha = "e" * 64
    release_id = release_id_override or compute_release_id(
        revision,
        wheel_sha,
        base.base_id,
        base_metadata_sha,
    )
    release = ReleaseMetadata(
        schema_version=1,
        release_id=release_id,
        runtime_revision=revision,
        wheel_sha256=wheel_sha,
        base_id=base.base_id,
        base_requirements_sha256=base.requirements_sha256,
        base_metadata_sha256=base_metadata_sha,
        app_freeze_sha256="f" * 64,
        created_at="2026-07-11T00:00:00Z",
        python_relative="venv/bin/python",
        purelib_relative="venv/lib/python/site-packages",
        base_link_relative="base",
        base_pth_relative="venv/lib/python/site-packages/codev_platform_base.pth",
    )
    release_dir = write_fake_release(root, release)
    base_link = release_dir / release.base_link_relative
    shutil.rmtree(base_link)
    base_link.symlink_to(base_dir, target_is_directory=True)
    return release_dir / "release.json", release, base


def _select_release(monkeypatch, release_file: Path, release: ReleaseMetadata) -> None:
    release_dir = release_file.parent
    monkeypatch.setattr(identity_module.sys, "prefix", str(release_dir / "venv"))
    monkeypatch.setattr(
        identity_module.sys,
        "executable",
        str(release_dir / release.python_relative),
    )


def _set_distribution(monkeypatch, distribution: _Distribution) -> None:
    monkeypatch.setattr(identity_module.metadata, "distribution", lambda name: distribution)


def _direct_url(root: Path, *, editable: bool = True) -> str:
    return json.dumps({"url": root.as_uri(), "dir_info": {"editable": editable}})


def test_environment_release_has_highest_priority_and_is_verified(monkeypatch, tmp_path: Path) -> None:
    release_file, release, _base = _write_release_tree(tmp_path / "runtime")
    current = tmp_path / "current"
    current.mkdir()
    (current / "venv").symlink_to(release_file.parent / "venv", target_is_directory=True)
    (current / "release.json").write_text("不是合法版本元数据", encoding="utf-8")
    monkeypatch.setattr(identity_module.sys, "prefix", str(current / "venv"))
    monkeypatch.setattr(identity_module.sys, "executable", str(current / release.python_relative))
    monkeypatch.setenv("CODEV_PLATFORM_RELEASE_FILE", str(release_file))
    monkeypatch.setattr(
        identity_module.metadata,
        "distribution",
        lambda name: (_ for _ in ()).throw(AssertionError("不应查询 distribution")),
    )

    identity = identity_module.runtime_identity()

    assert identity.mode == "release"
    assert identity.runtime_revision == release.runtime_revision
    assert identity.release_id == release.release_id
    assert identity.wheel_sha256 == release.wheel_sha256
    assert identity.base_id == release.base_id
    assert identity.base_requirements_sha256 == release.base_requirements_sha256
    assert identity.source_root is None


def test_adjacent_release_precedes_editable_and_installed(monkeypatch, tmp_path: Path) -> None:
    release_file, release, _base = _write_release_tree(tmp_path / "runtime")
    _select_release(monkeypatch, release_file, release)
    monkeypatch.setattr(
        identity_module.metadata,
        "distribution",
        lambda name: (_ for _ in ()).throw(AssertionError("邻接版本不应查询 distribution")),
    )

    assert identity_module.runtime_identity().release_id == release.release_id


def test_declared_release_failure_does_not_fall_back(monkeypatch, tmp_path: Path) -> None:
    missing = tmp_path / "secret-release" / "release.json"
    monkeypatch.setenv("CODEV_PLATFORM_RELEASE_FILE", str(missing))
    _set_distribution(monkeypatch, _Distribution(tmp_path / "dist", direct_url=None))

    with pytest.raises(RuntimeIdentityError) as caught:
        identity_module.runtime_identity()

    assert "secret-release" not in str(caught.value)
    assert caught.value.__cause__ is None
    rendered = "".join(traceback.format_exception(caught.value))
    assert "secret-release" not in rendered


@pytest.mark.parametrize("marker", ["release", "base"])
def test_release_rejects_incomplete_marker(monkeypatch, tmp_path: Path, marker: str) -> None:
    release_file, release, _base = _write_release_tree(tmp_path / "runtime")
    _select_release(monkeypatch, release_file, release)
    marker_root = release_file.parent if marker == "release" else release_file.parent / release.base_link_relative
    (marker_root / ".incomplete").write_text("", encoding="utf-8")

    with pytest.raises(RuntimeIdentityError):
        identity_module.runtime_identity()


@pytest.mark.parametrize("part", ["prefix", "executable"])
def test_release_rejects_prefix_or_interpreter_mismatch(
    monkeypatch,
    tmp_path: Path,
    part: str,
) -> None:
    release_file, release, _base = _write_release_tree(tmp_path / "runtime")
    _select_release(monkeypatch, release_file, release)
    monkeypatch.setenv("CODEV_PLATFORM_RELEASE_FILE", str(release_file))
    monkeypatch.setattr(identity_module.sys, part, str(tmp_path / "other"))

    with pytest.raises(RuntimeIdentityError):
        identity_module.runtime_identity()


def test_release_accepts_venv_python_final_symlink(monkeypatch, tmp_path: Path) -> None:
    system_python = Path(identity_module.sys.executable).resolve()
    release_file, release, _base = _write_release_tree(tmp_path / "runtime")
    declared_python = release_file.parent / release.python_relative
    declared_python.unlink()
    declared_python.symlink_to(system_python)
    _select_release(monkeypatch, release_file, release)

    identity = identity_module.runtime_identity()

    assert identity.interpreter_realpath == str(system_python)
    assert identity.environment_prefix == str((release_file.parent / "venv").resolve())


def test_release_rejects_venv_directory_symlink_escape(monkeypatch, tmp_path: Path) -> None:
    release_file, release, _base = _write_release_tree(tmp_path / "runtime")
    venv = release_file.parent / "venv"
    outside = tmp_path / "outside-venv"
    venv.rename(outside)
    venv.symlink_to(outside, target_is_directory=True)
    _select_release(monkeypatch, release_file, release)

    with pytest.raises(RuntimeIdentityError):
        identity_module.runtime_identity()


def test_release_rejects_recomputed_release_id_mismatch(monkeypatch, tmp_path: Path) -> None:
    release_file, release, _base = _write_release_tree(
        tmp_path / "runtime",
        release_id_override="1" * 64,
    )
    _select_release(monkeypatch, release_file, release)

    with pytest.raises(RuntimeIdentityError):
        identity_module.runtime_identity()


def test_release_rejects_base_metadata_drift(monkeypatch, tmp_path: Path) -> None:
    release_file, release, _base = _write_release_tree(tmp_path / "runtime")
    _select_release(monkeypatch, release_file, release)
    base_file = release_file.parent / release.base_link_relative / "base.json"
    base_file.write_bytes(base_file.read_bytes() + b" ")

    with pytest.raises(RuntimeIdentityError):
        identity_module.runtime_identity()


def test_release_rejects_base_link_escaping_runtime_root(monkeypatch, tmp_path: Path) -> None:
    release_file, release, _base = _write_release_tree(tmp_path / "runtime")
    _select_release(monkeypatch, release_file, release)
    base_link = release_file.parent / release.base_link_relative
    outside = tmp_path / "outside-base"
    outside.mkdir()
    shutil.copy2(base_link / "base.json", outside / "base.json")
    base_link.unlink()
    base_link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeIdentityError):
        identity_module.runtime_identity()


def test_release_rejects_expected_base_symlink_escaping_runtime_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    release_file, release, _base = _write_release_tree(tmp_path / "runtime")
    _select_release(monkeypatch, release_file, release)
    expected_base = release_file.parent.parent.parent / "bases" / release.base_id
    outside = tmp_path / "outside-expected-base"
    expected_base.rename(outside)
    expected_base.symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeIdentityError):
        identity_module.runtime_identity()


@pytest.mark.parametrize("mutation", ["missing", "drift", "escape"])
def test_release_rejects_missing_drifted_or_escaping_requirements_lock(
    monkeypatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    release_file, release, base = _write_release_tree(tmp_path / "runtime")
    _select_release(monkeypatch, release_file, release)
    lock_path = release_file.parent.parent.parent / "bases" / base.base_id / base.lock_relative
    if mutation == "missing":
        lock_path.unlink()
    elif mutation == "drift":
        lock_path.write_bytes(_LOCK_BYTES + b"changed")
    else:
        outside = tmp_path / "outside-requirements.lock"
        outside.write_bytes(_LOCK_BYTES)
        lock_path.unlink()
        lock_path.symlink_to(outside)

    with pytest.raises(RuntimeIdentityError):
        identity_module.runtime_identity()


def test_release_rejects_base_identity_or_abi_mismatch(monkeypatch, tmp_path: Path) -> None:
    release_file, release, base = _write_release_tree(tmp_path / "runtime")
    _select_release(monkeypatch, release_file, release)
    wrong_abi = dataclasses.replace(base.abi, machine=base.abi.machine + "-other")
    wrong_base = dataclasses.replace(base, abi=wrong_abi)
    base_file = release_file.parent / release.base_link_relative / "base.json"
    write_base_metadata_atomic(base_file, wrong_base)
    changed_sha = sha256_file(base_file)
    changed_release = dataclasses.replace(release, base_metadata_sha256=changed_sha)
    write_release_metadata_atomic(release_file, changed_release)

    with pytest.raises(RuntimeIdentityError):
        identity_module.runtime_identity()


@pytest.mark.parametrize("oid", ["1" * 40, "2" * 64])
@pytest.mark.parametrize("source_name", ["源码 空间", "literal%20"])
def test_editable_identity_uses_exact_read_only_git_probe(
    monkeypatch,
    tmp_path: Path,
    oid: str,
    source_name: str,
) -> None:
    source = tmp_path / source_name
    source.mkdir()
    _set_distribution(
        monkeypatch,
        _Distribution(tmp_path / "dist", direct_url=_direct_url(source)),
    )
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout=oid + "\n")

    monkeypatch.setattr(identity_module.subprocess, "run", fake_run)

    identity = identity_module.runtime_identity()

    assert identity.mode == "editable"
    assert identity.runtime_revision == oid
    assert identity.source_root == str(source.resolve())
    assert identity.release_id is None
    assert calls[0][0] == ["git", "rev-parse", "--verify", "HEAD^{commit}"]
    kwargs = calls[0][1]
    assert kwargs["cwd"] == source.resolve()
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["check"] is True
    assert kwargs["timeout"] == 3
    assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("failure", "output"),
    [
        (subprocess.TimeoutExpired(["git"], 3), None),
        (subprocess.CalledProcessError(1, ["git"]), None),
        (FileNotFoundError("git missing"), None),
        (None, "a" * 7 + "\n"),
        (None, "A" * 40 + "\n"),
        (None, "0" * 40 + "\n"),
        (None, "a" * 40 + "\nextra\n"),
    ],
)
def test_editable_git_failure_never_falls_back(
    monkeypatch,
    tmp_path: Path,
    failure: Exception | None,
    output: str | None,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _set_distribution(monkeypatch, _Distribution(tmp_path / "dist", direct_url=_direct_url(source)))

    def fake_run(argv, **kwargs):
        if failure is not None:
            raise failure
        return SimpleNamespace(stdout=output)

    monkeypatch.setattr(identity_module.subprocess, "run", fake_run)

    with pytest.raises(RuntimeIdentityError):
        identity_module.runtime_identity()


@pytest.mark.parametrize(
    "direct_url",
    [
        "not-json",
        json.dumps({"url": "file:", "dir_info": {"editable": True}}),
        json.dumps({"url": "file:relative/path", "dir_info": {"editable": True}}),
        json.dumps({"url": "file:////server/share", "dir_info": {"editable": True}}),
        json.dumps({"url": "https://example.invalid/repo", "dir_info": {"editable": True}}),
        json.dumps({"url": "file://remote-host/source", "dir_info": {"editable": True}}),
    ],
)
def test_invalid_editable_declaration_closes_failed(
    monkeypatch,
    tmp_path: Path,
    direct_url: str,
) -> None:
    (tmp_path / "relative" / "path").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    _set_distribution(monkeypatch, _Distribution(tmp_path / "dist", direct_url=direct_url))

    with pytest.raises(RuntimeIdentityError):
        identity_module.runtime_identity()


def test_invalid_direct_url_content_is_absent_from_traceback(monkeypatch, tmp_path: Path) -> None:
    secret = "secret-direct-url-content"
    _set_distribution(
        monkeypatch,
        _Distribution(tmp_path / "dist", direct_url='{"url":"' + secret),
    )

    with pytest.raises(RuntimeIdentityError) as caught:
        identity_module.runtime_identity()

    assert caught.value.__cause__ is None
    assert secret not in "".join(traceback.format_exception(caught.value))


def test_missing_editable_root_closes_failed(monkeypatch, tmp_path: Path) -> None:
    missing = tmp_path / "missing-source"
    _set_distribution(monkeypatch, _Distribution(tmp_path / "dist", direct_url=_direct_url(missing)))

    with pytest.raises(RuntimeIdentityError):
        identity_module.runtime_identity()


def test_non_editable_direct_url_uses_installed_record(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    distribution = _Distribution(
        tmp_path / "dist",
        direct_url=_direct_url(source, editable=False),
        record_bytes=b"raw-record\xff",
    )
    _set_distribution(monkeypatch, distribution)

    identity = identity_module.runtime_identity()

    record = Path(distribution.locate_file(distribution.files[0])).read_bytes()
    expected = hashlib.sha256(distribution.version.encode("utf-8") + b"\0" + record).hexdigest()
    assert identity.mode == "installed"
    assert identity.runtime_revision == expected
    assert identity.source_root is None


def test_installed_identity_hashes_version_nul_and_raw_record(monkeypatch, tmp_path: Path) -> None:
    distribution = _Distribution(
        tmp_path / "dist",
        direct_url=None,
        version="版本-1",
        record_bytes=b"line-one\r\nraw-\xff\x00\n",
    )
    _set_distribution(monkeypatch, distribution)

    identity = identity_module.runtime_identity()

    record = Path(distribution.locate_file(distribution.files[0])).read_bytes()
    expected = hashlib.sha256(distribution.version.encode("utf-8") + b"\0" + record).hexdigest()
    assert identity.runtime_revision == expected
    assert len(identity.runtime_revision) == 64


@pytest.mark.parametrize("records", [0, 2])
def test_installed_identity_rejects_missing_or_ambiguous_record(
    monkeypatch,
    tmp_path: Path,
    records: int,
) -> None:
    _set_distribution(
        monkeypatch,
        _Distribution(tmp_path / "dist", direct_url=None, records=records),
    )

    with pytest.raises(RuntimeIdentityError):
        identity_module.runtime_identity()


def test_identity_is_cached_once_per_process(monkeypatch, tmp_path: Path) -> None:
    distribution = _Distribution(tmp_path / "dist", direct_url=None)
    _set_distribution(monkeypatch, distribution)

    first = identity_module.runtime_identity()
    distribution.version = "changed-after-start"
    second = identity_module.runtime_identity()

    assert second is first
    assert identity_module.runtime_identity.cache_info().maxsize == 1
    identity_module.runtime_identity.cache_clear()
    assert identity_module.runtime_identity().runtime_revision != first.runtime_revision
