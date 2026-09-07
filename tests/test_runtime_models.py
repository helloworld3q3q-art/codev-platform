"""运行时叶子模型、严格编解码与原子写契约测试。"""
from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform.core import runtime_models as models
from codev_platform.core import runtime_metadata_io as metadata_io
from codev_platform.core.runtime_models import (
    RUNTIME_ACCESS_PROFILE,
    ActivationResult,
    BaseMetadata,
    ReleaseCandidate,
    ReleaseMetadata,
    RequirementsLockInfo,
    RuntimeAbi,
    RuntimeIdentity,
    RuntimeModelError,
    SystemdRuntime,
)
from codev_platform.gateway.auth import Unauthorized
from tests.runtime_support import (
    AcceptingAuthenticator,
    RejectingAuthenticator,
    assert_path_inside,
    init_git_repo,
    minimal_config,
    write_fake_base,
    write_fake_release,
    write_json,
)


def _abi() -> RuntimeAbi:
    return RuntimeAbi(
        implementation="cpython",
        python_version="3.11.9",
        cache_tag="cpython-311",
        soabi="cp311-win_amd64",
        platform_tag="win-amd64",
        machine="AMD64",
    )


def _base() -> BaseMetadata:
    return BaseMetadata(
        schema_version=3,
        access_profile=RUNTIME_ACCESS_PROFILE,
        base_id="b" * 64,
        requirements_sha256="a" * 64,
        approved_index_url="https://pypi.org/simple",
        artifact_manifest_sha256="c" * 64,
        freeze_sha256="d" * 64,
        purelib_inventory_sha256="e" * 64,
        abi=_abi(),
        created_at="2026-07-11T00:00:00Z",
        python_relative="venv/bin/python",
        purelib_relative="venv/lib/python/site-packages",
        bin_relative="venv/bin",
        lock_relative="requirements.lock",
    )


def _candidate(runtime_revision: str = "e" * 40) -> ReleaseCandidate:
    return ReleaseCandidate(
        schema_version=1,
        runtime_revision=runtime_revision,
        wheel_name="codev_platform-1.0-py3-none-any.whl",
        wheel_sha256="f" * 64,
    )


def _release() -> ReleaseMetadata:
    return ReleaseMetadata(
        schema_version=1,
        release_id="1" * 64,
        runtime_revision="2" * 40,
        wheel_sha256="3" * 64,
        base_id="4" * 64,
        base_requirements_sha256="5" * 64,
        base_metadata_sha256="6" * 64,
        app_freeze_sha256="7" * 64,
        created_at="2026-07-11T00:00:00Z",
        python_relative="venv/bin/python",
        purelib_relative="venv/lib/python/site-packages",
        base_link_relative="base",
        base_pth_relative="venv/lib/python/site-packages/base-runtime.pth",
    )


def _identity(mode: models.RuntimeMode) -> RuntimeIdentity:
    release_mode = mode == "release"
    return RuntimeIdentity(
        mode=mode,
        runtime_revision="a" * 40,
        release_id="b" * 64 if release_mode else None,
        wheel_sha256="c" * 64 if release_mode else None,
        base_id="d" * 64 if release_mode else None,
        base_requirements_sha256="e" * 64 if release_mode else None,
        interpreter_realpath="/runtime/python",
        environment_prefix="/runtime/venv",
        source_root="/workspace/source" if mode == "editable" else None,
    )


def test_model_fields_are_exact_and_frozen() -> None:
    expected = {
        RuntimeAbi: ("implementation", "python_version", "cache_tag", "soabi", "platform_tag", "machine"),
        RequirementsLockInfo: (
            "requirements_sha256", "approved_index_url", "pin_count",
            "artifact_manifest_sha256", "cuda_tags",
        ),
        BaseMetadata: (
            "schema_version", "access_profile", "base_id", "requirements_sha256",
            "approved_index_url", "artifact_manifest_sha256", "freeze_sha256",
            "purelib_inventory_sha256", "abi", "created_at",
            "python_relative", "purelib_relative", "bin_relative", "lock_relative",
        ),
        ReleaseCandidate: ("schema_version", "runtime_revision", "wheel_name", "wheel_sha256"),
        ReleaseMetadata: (
            "schema_version", "release_id", "runtime_revision", "wheel_sha256", "base_id",
            "base_requirements_sha256", "base_metadata_sha256", "app_freeze_sha256",
            "created_at", "python_relative", "purelib_relative", "base_link_relative",
            "base_pth_relative",
        ),
        RuntimeIdentity: (
            "mode", "runtime_revision", "release_id", "wheel_sha256", "base_id",
            "base_requirements_sha256", "interpreter_realpath", "environment_prefix", "source_root",
        ),
        ActivationResult: ("active_release", "previous_release"),
        SystemdRuntime: ("release_root",),
    }
    for model_type, names in expected.items():
        assert tuple(field.name for field in dataclasses.fields(model_type)) == names
        assert model_type.__dataclass_params__.frozen is True
    with pytest.raises(dataclasses.FrozenInstanceError):
        _candidate().wheel_name = "other.whl"


def test_requirements_lock_info_accepts_valid_leaf_value() -> None:
    lock_info = RequirementsLockInfo(
        requirements_sha256="a" * 64,
        approved_index_url="https://pypi.org/simple",
        pin_count=2,
        artifact_manifest_sha256="b" * 64,
        cuda_tags=frozenset({"cpu", "cu128"}),
    )

    assert RequirementsLockInfo(**dataclasses.asdict(lock_info)) == lock_info


@pytest.mark.parametrize("value", ["a" * 40, "b" * 64])
def test_runtime_revision_accepts_full_lowercase_oid(value: str) -> None:
    assert _candidate(value).runtime_revision == value


@pytest.mark.parametrize("value", ["a" * 7, "0" * 40, "A" * 40, "g" * 40, "a" * 63])
def test_runtime_revision_rejects_invalid_oid(value: str) -> None:
    with pytest.raises(RuntimeModelError):
        _candidate(value)


@pytest.mark.parametrize("value", ["a" * 63, "0" * 64, "A" * 64, "z" * 64])
def test_sha256_and_ids_reject_invalid_values(value: str) -> None:
    with pytest.raises(RuntimeModelError):
        dataclasses.replace(_base(), base_id=value)


@pytest.mark.parametrize("schema", [0, -1, 2, True, "1"])
def test_schema_requires_positive_integer(schema: object) -> None:
    values = dataclasses.asdict(_candidate())
    values["schema_version"] = schema
    with pytest.raises(RuntimeModelError):
        ReleaseCandidate(**values)


def test_base_metadata_requires_schema_v3_and_exact_access_profile() -> None:
    base = _base()

    assert RUNTIME_ACCESS_PROFILE == "root-service-group-read-v1"
    assert base.access_profile == RUNTIME_ACCESS_PROFILE
    with pytest.raises(RuntimeModelError, match="整数 3"):
        dataclasses.replace(base, schema_version=2)
    with pytest.raises(RuntimeModelError, match="access_profile"):
        dataclasses.replace(base, access_profile="root-service-private-v1")


@pytest.mark.parametrize("value", ["", "/absolute/path", "../escape", "safe/../../escape"])
def test_relative_paths_reject_empty_absolute_or_escape(value: str) -> None:
    values = dataclasses.asdict(_candidate())
    values["wheel_name"] = value
    with pytest.raises(RuntimeModelError):
        ReleaseCandidate(**values)


def test_current_abi_returns_complete_leaf_value() -> None:
    abi = models.current_abi()
    assert all(isinstance(value, str) and value for value in dataclasses.asdict(abi).values())


def test_current_abi_fails_closed_when_soabi_is_missing(monkeypatch) -> None:
    real_get = models.sysconfig.get_config_var
    monkeypatch.setattr(
        models.sysconfig,
        "get_config_var",
        lambda name: None if name == "SOABI" else real_get(name),
    )
    with pytest.raises(RuntimeModelError, match="SOABI"):
        models.current_abi()


def test_current_abi_fails_closed_when_machine_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(models.platform, "machine", lambda: "")

    with pytest.raises(RuntimeModelError, match="machine"):
        models.current_abi()


def test_canonical_json_and_sha_are_stable() -> None:
    value = {"b": 1, "a": "中"}
    encoded = b'{"a":"\\u4e2d","b":1}'
    assert models.canonical_json_bytes(value) == encoded
    assert models.canonical_sha256(value) == hashlib.sha256(encoded).hexdigest()
    with pytest.raises(RuntimeModelError):
        models.canonical_json_bytes({"bad": float("nan")})


def test_fixed_base_and_release_id_vectors() -> None:
    base_id = models.compute_base_id("a" * 64, _abi())
    expected_payload = {
        "abi": _abi(),
        "access_profile": RUNTIME_ACCESS_PROFILE,
        "requirements_sha256": "a" * 64,
        "schema_version": 3,
    }
    legacy_payload = {
        "abi": _abi(),
        "requirements_sha256": "a" * 64,
        "schema_version": 2,
    }
    other_profile_payload = {**expected_payload, "access_profile": "root-service-private-v1"}

    assert tuple(inspect.signature(models.compute_base_id).parameters) == (
        "requirements_sha256",
        "abi",
    )
    assert base_id == "2b4cfc03c98b0e3eb373096d58bdfb9dbb105c94e54ea11cd8d0602981c55aa8"
    assert base_id == models.canonical_sha256(expected_payload)
    assert base_id != models.canonical_sha256(legacy_payload)
    assert base_id != models.canonical_sha256(other_profile_payload)
    release_id = models.compute_release_id("b" * 40, "c" * 64, base_id, "d" * 64)
    assert release_id == "d7ab0bc9cb7b1e76eb20e6e3490cc14f73824a8d96048a483e5d5bc19817f5de"
    assert len(base_id) == len(release_id) == 64


def test_sha256_file_streams_exact_bytes(tmp_path: Path) -> None:
    path = tmp_path / "artifact.whl"
    path.write_bytes(b"runtime-artifact")
    assert models.sha256_file(path) == hashlib.sha256(b"runtime-artifact").hexdigest()


def test_typed_codecs_round_trip_all_metadata(tmp_path: Path) -> None:
    cases = (
        (tmp_path / "base.json", _base(), models.write_base_metadata_atomic, models.read_base_metadata),
        (
            tmp_path / "candidate.json", _candidate(),
            models.write_release_candidate_atomic, models.read_release_candidate,
        ),
        (
            tmp_path / "release.json", _release(),
            models.write_release_metadata_atomic, models.read_release_metadata,
        ),
    )
    for path, expected, writer, reader in cases:
        writer(path, expected)
        assert reader(path) == expected
        assert path.read_bytes() == models.canonical_json_bytes(expected)


def test_typed_bytes_decoder_uses_exact_schema() -> None:
    candidate = _candidate()
    decoded = metadata_io.decode_typed_bytes(
        models.canonical_json_bytes(candidate),
        ReleaseCandidate,
    )

    assert decoded == candidate
    with pytest.raises(RuntimeModelError):
        metadata_io.decode_typed_bytes(b'{"schema_version":1}', ReleaseCandidate)


def test_base_decoder_rejects_legacy_schema_v2_bytes() -> None:
    legacy_payload = dataclasses.asdict(_base())
    legacy_payload["schema_version"] = 2
    legacy_payload.pop("access_profile")
    assert set(legacy_payload) == {
        "schema_version", "base_id", "requirements_sha256", "approved_index_url",
        "artifact_manifest_sha256", "freeze_sha256", "purelib_inventory_sha256",
        "abi", "created_at", "python_relative", "purelib_relative", "bin_relative",
        "lock_relative",
    }
    legacy_bytes = json.dumps(legacy_payload, sort_keys=True, separators=(",", ":")).encode()

    assert b'"access_profile"' not in legacy_bytes
    with pytest.raises(RuntimeModelError, match="BaseMetadata 元数据无效"):
        metadata_io.decode_typed_bytes(legacy_bytes, BaseMetadata)

    schema_v3_without_profile = {**legacy_payload, "schema_version": 3}
    with pytest.raises(RuntimeModelError, match="BaseMetadata 元数据无效"):
        metadata_io.decode_typed_bytes(
            json.dumps(schema_v3_without_profile).encode(),
            BaseMetadata,
        )

    schema_v2_with_profile = {
        **legacy_payload,
        "access_profile": RUNTIME_ACCESS_PROFILE,
    }
    with pytest.raises(RuntimeModelError, match="BaseMetadata 元数据无效"):
        metadata_io.decode_typed_bytes(
            json.dumps(schema_v2_with_profile).encode(),
            BaseMetadata,
        )


@pytest.mark.parametrize("mutation", ["missing", "unknown", "wrong_type", "bad_abi"])
def test_base_decoder_rejects_non_exact_json(tmp_path: Path, mutation: str) -> None:
    payload = dataclasses.asdict(_base())
    if mutation == "missing":
        payload.pop("base_id")
    elif mutation == "unknown":
        payload["extra"] = "forbidden"
    elif mutation == "wrong_type":
        payload["schema_version"] = True
    else:
        payload["abi"] = ["not", "an", "object"]
    path = tmp_path / "base.json"
    write_json(path, payload)
    with pytest.raises(RuntimeModelError):
        models.read_base_metadata(path)


def test_decoder_error_does_not_leak_file_content(tmp_path: Path) -> None:
    path = tmp_path / "base.json"
    secret = "sensitive-file-content"
    path.write_text('{"secret":"' + secret + '"}', encoding="utf-8")
    with pytest.raises(RuntimeModelError) as caught:
        models.read_base_metadata(path)
    assert secret not in str(caught.value)


def test_atomic_write_replaces_and_fsyncs_parent(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "candidate.json"
    path.write_text("old", encoding="utf-8")
    seen: list[Path] = []
    monkeypatch.setattr(metadata_io, "_fsync_directory", lambda parent: seen.append(parent))
    models.write_release_candidate_atomic(path, _candidate())
    assert models.read_release_candidate(path) == _candidate()
    assert seen == [tmp_path]
    assert list(tmp_path.glob(f".{path.name}.*.tmp")) == []


def test_atomic_write_failure_preserves_old_file_and_cleans_temp(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "candidate.json"
    path.write_bytes(b"old-value")

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(metadata_io.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        models.write_release_candidate_atomic(path, _candidate())
    assert path.read_bytes() == b"old-value"
    assert list(tmp_path.glob(f".{path.name}.*.tmp")) == []


def test_atomic_write_create_collision_preserves_foreign_temp(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "candidate.json"
    fixed_hex = "a" * 32
    temp = tmp_path / f".{path.name}.{fixed_hex}.tmp"
    temp.write_bytes(b"foreign-temp")
    monkeypatch.setattr(metadata_io, "uuid4", lambda: SimpleNamespace(hex=fixed_hex))

    with pytest.raises(FileExistsError):
        models.write_release_candidate_atomic(path, _candidate())

    assert temp.read_bytes() == b"foreign-temp"


def test_fsync_directory_opens_fsyncs_and_closes_same_descriptor(tmp_path: Path, monkeypatch) -> None:
    opened: list[tuple[Path, int]] = []
    fsynced: list[int] = []
    closed: list[int] = []
    descriptor = 37
    flags = metadata_io.os.O_RDONLY | getattr(metadata_io.os, "O_DIRECTORY", 0)
    monkeypatch.setattr(
        metadata_io.os,
        "open",
        lambda path, got_flags: opened.append((path, got_flags)) or descriptor,
    )
    monkeypatch.setattr(metadata_io.os, "fsync", fsynced.append)
    monkeypatch.setattr(metadata_io.os, "close", closed.append)

    metadata_io._fsync_directory(tmp_path)

    assert opened == [(tmp_path, flags)]
    assert fsynced == [descriptor]
    assert closed == [descriptor]


def test_runtime_identity_dict_and_systemd_python(tmp_path: Path) -> None:
    identity = RuntimeIdentity(
        mode="release",
        runtime_revision="a" * 40,
        release_id="b" * 64,
        wheel_sha256="c" * 64,
        base_id="d" * 64,
        base_requirements_sha256="e" * 64,
        interpreter_realpath=str(tmp_path / "python"),
        environment_prefix=str(tmp_path / "venv"),
        source_root=None,
    )
    assert identity.as_dict() == dataclasses.asdict(identity)
    assert SystemdRuntime(tmp_path).python == tmp_path / "current" / "venv" / "bin" / "python"


@pytest.mark.parametrize("mode", ["release", "editable", "installed"])
def test_runtime_identity_accepts_mode_consistent_fields(mode: models.RuntimeMode) -> None:
    assert _identity(mode).mode == mode


@pytest.mark.parametrize(
    ("mode", "changes"),
    [
        ("release", {"release_id": None}),
        ("release", {"wheel_sha256": None}),
        ("release", {"base_id": None}),
        ("release", {"base_requirements_sha256": None}),
        ("release", {"source_root": "/workspace/source"}),
        ("editable", {"source_root": None}),
        ("editable", {"release_id": "b" * 64}),
        ("editable", {"wheel_sha256": "c" * 64}),
        ("editable", {"base_id": "d" * 64}),
        ("editable", {"base_requirements_sha256": "e" * 64}),
        ("installed", {"source_root": "/workspace/source"}),
        ("installed", {"release_id": "b" * 64}),
        ("installed", {"wheel_sha256": "c" * 64}),
        ("installed", {"base_id": "d" * 64}),
        ("installed", {"base_requirements_sha256": "e" * 64}),
    ],
)
def test_runtime_identity_rejects_mode_inconsistent_fields(
    mode: models.RuntimeMode,
    changes: dict[str, object],
) -> None:
    with pytest.raises(RuntimeModelError, match="mode"):
        dataclasses.replace(_identity(mode), **changes)


def test_runtime_support_helpers_are_concrete(tmp_path: Path) -> None:
    json_path = tmp_path / "value.json"
    assert write_json(json_path, {"value": 1}) == json_path
    assert json.loads(json_path.read_text(encoding="utf-8")) == {"value": 1}
    repo = init_git_repo(tmp_path / "repo")
    oid = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    assert len(oid) in {40, 64} and int(oid, 16) > 0
    assert minimal_config(tmp_path) == {
        "runtime": {
            "release_root": str(tmp_path),
            "chroma_venv": str(tmp_path / "legacy" / ".venv"),
        },
        "projects": {},
    }
    identity = AcceptingAuthenticator().authenticate({})
    assert (identity.user_id, identity.org_id, identity.via) == (
        "runtime-user", "runtime-org", "test",
    )
    with pytest.raises(Unauthorized):
        RejectingAuthenticator().authenticate({})
    base = _base()
    release = _release()
    base_dir = write_fake_base(tmp_path / "runtime", base)
    release_dir = write_fake_release(tmp_path / "runtime", release)
    assert base_dir == tmp_path / "runtime" / "bases" / base.base_id
    assert release_dir == tmp_path / "runtime" / "releases" / release.release_id
    assert (base_dir / "base.json").exists()
    assert (release_dir / "release.json").exists()
    assert_path_inside(
        path=tmp_path / "runtime" / "releases",
        parent=tmp_path / "runtime",
    )
    with pytest.raises(AssertionError):
        assert_path_inside(path=tmp_path / "outside", parent=tmp_path / "runtime")
