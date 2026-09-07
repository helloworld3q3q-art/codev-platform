"""受审 CUDA 依赖 hash lock 的构建与验证契约测试。"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from codev_platform import runtime_lock
from codev_platform.runtime_artifact_staging import RuntimeArtifactStagingError


_APPROVED_INDEX = "https://download.pytorch.org/whl/cu128"
_PRIMARY_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
_VERSIONS = {
    "alembic": "1.13.3",
    "anthropic": "0.40.0",
    "chromadb": "1.5.9",
    "cryptography": "48.0.0",
    "fastapi": "0.115.0",
    "httptools": "0.7.1",
    "jieba": "0.42.1",
    "mcp": "1.26.0",
    "mcp-proxy": "0.12.0",
    "openai": "1.55.0",
    "psycopg": "3.2.9",
    "psycopg-binary": "3.2.9",
    "psycopg-pool": "3.2.9",
    "pydantic": "2.12.5",
    "rank-bm25": "0.2.2",
    "sentence-transformers": "5.5.1",
    "sqlalchemy": "2.0.41",
    "torch": "2.11.0+cu128",
    "uvicorn": "0.34.0",
    "watchfiles": "1.2.0",
    "websockets": "16.0",
}


def _write_approved(path: Path, *lines: str) -> Path:
    selected = lines or (
        f"--index-url {_PRIMARY_INDEX}",
        f"--extra-index-url {_APPROVED_INDEX}",
    )
    path.write_text("\n".join(selected) + "\n", encoding="utf-8")
    return path


def _write_freeze(
    path: Path,
    *,
    changes: dict[str, str | None] | None = None,
    extra: tuple[str, ...] = (),
) -> Path:
    versions = dict(_VERSIONS)
    for name, value in (changes or {}).items():
        if value is None:
            versions.pop(name, None)
        else:
            versions[name] = value
    lines = [f"{name}=={version}" for name, version in sorted(versions.items())]
    path.write_text("\n".join([*lines, *extra]) + "\n", encoding="utf-8")
    return path


def _fake_downloader(
    requirement: str,
    download_dir: Path,
    approved_index_url: str,
    primary_index_url: str,
    _cancellation=None,
) -> Path:
    assert approved_index_url == _APPROVED_INDEX
    assert primary_index_url == _PRIMARY_INDEX
    name, version = requirement.split("==", 1)
    filename = f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
    artifact = download_dir / filename
    artifact.write_bytes(f"artifact:{requirement}".encode())
    return artifact


def _build_valid_lock(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path]:
    approved = _write_approved(tmp_path / "requirements-runtime.txt")
    freeze = _write_freeze(tmp_path / "runtime.freeze")
    lock = tmp_path / "wsl-runtime.lock"
    monkeypatch.setattr(runtime_lock, "_download_one", _fake_downloader)
    runtime_lock.build_hashed_lock(freeze, approved, lock, tmp_path / "artifacts")
    return lock, approved, freeze


def test_real_downloader_uses_isolated_binary_only_pip_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    wheel = artifacts / "torch-2.11.0+cu128-py3-none-any.whl"
    commands: list[tuple[str, ...]] = []

    def run(command, **kwargs):
        commands.append(tuple(command))
        assert kwargs["timeout"] > 0
        assert kwargs["environment"]["PYTHONNOUSERSITE"] == "1"
        assert not kwargs["cancellation"].cancelled
        wheel.write_bytes(b"small-test-wheel")

    monkeypatch.setattr(runtime_lock, "run_cancellable_process", run)

    selected = runtime_lock._download_one(
        "torch==2.11.0+cu128",
        artifacts,
        _APPROVED_INDEX,
        _PRIMARY_INDEX,
    )

    assert selected == wheel
    assert len(commands) == 1
    command = commands[0]
    assert "--isolated" in command
    assert "--no-deps" in command
    assert "--only-binary=:all:" in command
    assert command[-3:] == (
        "--index-url",
        _APPROVED_INDEX,
        "torch==2.11.0+cu128",
    )
    assert _PRIMARY_INDEX not in command


def test_downloader_reuses_unique_existing_wheel_without_network(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    wheel = artifacts / "torch-2.11.0+cu128-py3-none-any.whl"
    wheel.write_bytes(b"approved-local-wheel")
    monkeypatch.setattr(
        runtime_lock,
        "run_cancellable_process",
        lambda *_args, **_kwargs: pytest.fail("已有唯一 wheel 时不得联网"),
    )

    assert runtime_lock._download_one(
        "torch==2.11.0+cu128",
        artifacts,
        _APPROVED_INDEX,
        _PRIMARY_INDEX,
    ) == wheel


def test_downloader_rejects_existing_wheel_for_other_python_abi(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    incompatible = artifacts / "torch-2.11.0+cu128-cp27-cp27m-manylinux1_x86_64.whl"
    incompatible.write_bytes(b"wrong-abi")
    monkeypatch.setattr(
        runtime_lock,
        "run_cancellable_process",
        lambda *_args, **_kwargs: pytest.fail("测试只验证不兼容缓存不得被复用"),
    )

    with pytest.raises(runtime_lock.RuntimeLockError, match="ABI 不兼容"):
        runtime_lock._download_one(
            "torch==2.11.0+cu128",
            artifacts,
            _APPROVED_INDEX,
            _PRIMARY_INDEX,
        )


def test_standard_package_download_only_uses_primary_mirror(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    wheel = artifacts / "demo-1.0-py3-none-any.whl"
    commands: list[tuple[str, ...]] = []

    def run(command, **_kwargs):
        commands.append(tuple(command))
        wheel.write_bytes(b"demo")

    monkeypatch.setattr(runtime_lock, "run_cancellable_process", run)

    assert runtime_lock._download_one(
        "demo==1.0",
        artifacts,
        _APPROVED_INDEX,
        _PRIMARY_INDEX,
    ) == wheel
    assert _PRIMARY_INDEX in commands[0]
    assert _APPROVED_INDEX not in commands[0]


def test_lock_build_rejects_extra_wheel_before_download(tmp_path: Path, monkeypatch) -> None:
    approved = _write_approved(tmp_path / "requirements-runtime.txt")
    freeze = _write_freeze(tmp_path / "runtime.freeze")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "rogue-1.0-py3-none-any.whl").write_bytes(b"rogue")
    monkeypatch.setattr(
        runtime_lock,
        "_download_one",
        lambda *_args: pytest.fail("额外制品门禁失败前不得下载"),
    )

    with pytest.raises(runtime_lock.RuntimeLockError, match="额外"):
        runtime_lock.build_hashed_lock(
            freeze,
            approved,
            tmp_path / "runtime.lock",
            artifacts,
        )


def test_published_wheelhouse_is_never_filled_in_place(
    tmp_path: Path,
    monkeypatch,
) -> None:
    approved = _write_approved(tmp_path / "requirements-runtime.txt")
    freeze = _write_freeze(tmp_path / "runtime.freeze")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    existing = artifacts / "mcp-1.26.0-py3-none-any.whl"
    existing.write_bytes(b"existing")
    before = {path.name: path.read_bytes() for path in artifacts.iterdir()}
    monkeypatch.setattr(
        runtime_lock,
        "_download_one",
        lambda *_args: pytest.fail("已发布 wheelhouse 不得原地下载"),
    )

    with pytest.raises(runtime_lock.RuntimeLockError, match="不完整"):
        runtime_lock.build_hashed_lock(
            freeze,
            approved,
            tmp_path / "runtime.lock",
            artifacts,
        )

    assert {path.name: path.read_bytes() for path in artifacts.iterdir()} == before


def test_wheelhouse_publish_failure_does_not_expose_new_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    approved = _write_approved(tmp_path / "requirements-runtime.txt")
    freeze = _write_freeze(tmp_path / "runtime.freeze")
    output = tmp_path / "runtime.lock"
    artifacts = tmp_path / "artifacts"

    @contextmanager
    def failing_workspace(_final: Path):
        working = tmp_path / ".artifacts.incomplete"
        working.mkdir()

        class Workspace:
            publish_required = True

            def __init__(self) -> None:
                self.working = working

            def publish(self) -> None:
                raise RuntimeArtifactStagingError("注入发布失败")

        yield Workspace()

    monkeypatch.setattr(runtime_lock, "locked_artifact_workspace", failing_workspace)
    monkeypatch.setattr(runtime_lock, "_download_one", _fake_downloader)

    with pytest.raises(runtime_lock.RuntimeLockError, match="注入发布失败"):
        runtime_lock.build_hashed_lock(freeze, approved, output, artifacts)

    assert not output.exists()
    assert (tmp_path / ".runtime.lock.incomplete").is_file()


def test_锁输出并发锁必须包住独立wheelhouse事务(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = _write_approved(tmp_path / "requirements-runtime.txt")
    freeze = _write_freeze(tmp_path / "runtime.freeze")
    output = tmp_path / "runtime.lock"
    artifacts = tmp_path / "artifacts"
    events: list[str] = []
    real_publication = runtime_lock.locked_artifact_publication
    real_workspace = runtime_lock.locked_artifact_workspace

    @contextmanager
    def publication(path: Path):
        events.append("output_enter")
        with real_publication(path) as selected:
            yield selected
        events.append("output_exit")

    @contextmanager
    def workspace(path: Path):
        events.append("wheelhouse_enter")
        with real_workspace(path) as selected:
            yield selected
        events.append("wheelhouse_exit")

    monkeypatch.setattr(runtime_lock, "locked_artifact_publication", publication)
    monkeypatch.setattr(runtime_lock, "locked_artifact_workspace", workspace)
    monkeypatch.setattr(runtime_lock, "_download_one", _fake_downloader)

    runtime_lock.build_hashed_lock(freeze, approved, output, artifacts)

    assert events == [
        "output_enter",
        "wheelhouse_enter",
        "wheelhouse_exit",
        "output_exit",
    ]


def test_build_hashed_lock_is_deterministic_and_round_trips(tmp_path: Path, monkeypatch) -> None:
    approved = _write_approved(tmp_path / "requirements-runtime.txt")
    freeze = _write_freeze(tmp_path / "runtime.freeze")
    lock = tmp_path / "wsl-runtime.lock"
    artifacts = tmp_path / "artifacts"
    calls: list[str] = []

    def download(
        requirement: str,
        download_dir: Path,
        approved_index_url: str,
        primary_index_url: str,
        cancellation=None,
    ) -> Path:
        calls.append(requirement)
        return _fake_downloader(
            requirement,
            download_dir,
            approved_index_url,
            primary_index_url,
            cancellation,
        )

    monkeypatch.setattr(runtime_lock, "_download_one", download)
    built = runtime_lock.build_hashed_lock(freeze, approved, lock, artifacts)
    verified = runtime_lock.validate_requirements_lock(lock, approved)
    contract = runtime_lock.validate_requirements_lock_contract(lock, approved)

    assert built == verified
    assert contract.info == verified
    assert tuple(pin.requirement for pin in contract.pins) == tuple(
        f"{name}=={_VERSIONS[name]}" for name in sorted(_VERSIONS)
    )
    assert len(calls) == len(_VERSIONS)
    assert set(calls) == {f"{name}=={version}" for name, version in _VERSIONS.items()}
    assert built.pin_count == len(_VERSIONS)
    assert built.cuda_tags == frozenset({"cu128"})
    assert built.approved_index_url == _APPROVED_INDEX
    assert built.requirements_sha256 == hashlib.sha256(lock.read_bytes()).hexdigest()
    manifest = [
        {"filename": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(artifacts.glob("*.whl"), key=lambda item: item.name)
    ]
    canonical = json.dumps(
        manifest,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert built.artifact_manifest_sha256 == hashlib.sha256(canonical).hexdigest()
    content = lock.read_text(encoding="utf-8")
    assert f"--index-url {_PRIMARY_INDEX}" in content
    assert f"--extra-index-url {_APPROVED_INDEX}" in content
    assert all(
        f"{name}=={version} --hash=sha256:" in content for name, version in _VERSIONS.items()
    )


def test_inspect_saved_lock_recomputes_identity_without_approved_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    lock, approved, _freeze = _build_valid_lock(tmp_path, monkeypatch)
    expected = runtime_lock.validate_requirements_lock(lock, approved)
    approved.unlink()

    inspected = runtime_lock.inspect_requirements_lock(lock, _APPROVED_INDEX)

    assert inspected == expected


def test_inspect解析与摘要只使用同一次文件快照(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock, _approved, _freeze = _build_valid_lock(tmp_path, monkeypatch)
    original = lock.read_bytes()
    calls = 0
    read_snapshot = runtime_lock._parsing._read_file_snapshot

    def mutate_after_snapshot(path: Path, *, kind: str) -> bytes:
        nonlocal calls
        calls += 1
        content = read_snapshot(path, kind=kind)
        path.write_bytes(content + b"# concurrent replacement\n")
        return content

    monkeypatch.setattr(
        runtime_lock._parsing,
        "_read_file_snapshot",
        mutate_after_snapshot,
    )

    inspected = runtime_lock.inspect_requirements_lock(
        lock,
        _APPROVED_INDEX,
        _PRIMARY_INDEX,
    )

    assert calls == 1
    assert inspected.requirements_sha256 == hashlib.sha256(original).hexdigest()


@pytest.mark.parametrize(
    "expected_index",
    [
        "http://download.pytorch.org/whl/cu128",
        "https://placeholder@download.pytorch.org/whl/cu128",
        "https://example.invalid/whl/cu128",
        "https://download.pytorch.org/whl/cu129",
    ],
)
def test_inspect_rejects_unapproved_or_lock_mismatched_expected_index(
    tmp_path: Path,
    monkeypatch,
    expected_index: str,
) -> None:
    lock, _approved, _freeze = _build_valid_lock(tmp_path, monkeypatch)

    with pytest.raises(runtime_lock.RuntimeLockError, match="索引"):
        runtime_lock.inspect_requirements_lock(lock, expected_index)


@pytest.mark.parametrize(
    "index_lines",
    [
        (),
        (
            f"--extra-index-url {_APPROVED_INDEX}",
            f"--extra-index-url {_APPROVED_INDEX}",
        ),
        (
            f"--extra-index-url {_APPROVED_INDEX}",
            f"--extra-index-url {_APPROVED_INDEX} unexpected",
        ),
        ("--extra-index-url http://download.pytorch.org/whl/cu128",),
        ("--extra-index-url https://placeholder@download.pytorch.org/whl/cu128",),
        ("--extra-index-url https://example.invalid/whl/cu128",),
        ("--extra-index-url https://download.pytorch.org/whl/cu128?channel=other",),
    ],
)
def test_build_rejects_non_unique_or_unapproved_cuda_index(
    tmp_path: Path,
    monkeypatch,
    index_lines: tuple[str, ...],
) -> None:
    approved = tmp_path / "requirements-runtime.txt"
    approved.write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    freeze = _write_freeze(tmp_path / "runtime.freeze")
    monkeypatch.setattr(
        runtime_lock,
        "_download_one",
        lambda *_args: pytest.fail("索引门禁失败前不得下载制品"),
    )

    with pytest.raises(runtime_lock.RuntimeLockError, match="批准索引"):
        runtime_lock.build_hashed_lock(
            freeze,
            approved,
            tmp_path / "runtime.lock",
            tmp_path / "artifacts",
        )


@pytest.mark.parametrize(
    "primary_line",
    (
        "--index-url http://pypi.tuna.tsinghua.edu.cn/simple",
        "--index-url https://example.invalid/simple",
        "--index-url https://user@pypi.org/simple",
    ),
)
def test_build_rejects_unapproved_primary_index(
    tmp_path: Path,
    monkeypatch,
    primary_line: str,
) -> None:
    approved = _write_approved(
        tmp_path / "requirements-runtime.txt",
        primary_line,
        f"--extra-index-url {_APPROVED_INDEX}",
    )
    freeze = _write_freeze(tmp_path / "runtime.freeze")
    monkeypatch.setattr(
        runtime_lock,
        "_download_one",
        lambda *_args: pytest.fail("主镜像门禁失败前不得下载制品"),
    )

    with pytest.raises(runtime_lock.RuntimeLockError, match="主镜像"):
        runtime_lock.build_hashed_lock(
            freeze,
            approved,
            tmp_path / "runtime.lock",
            tmp_path / "artifacts",
        )


@pytest.mark.parametrize(
    "unsafe_line",
    [
        "unknown>=1.0",
        "unknown @ file:///tmp/unknown.whl",
        "unknown @ https://example.invalid/unknown.whl",
        "-e file:///tmp/unknown#egg=unknown",
        "git+https://example.invalid/unknown.git@main#egg=unknown",
        "--find-links /tmp/wheels",
    ],
)
def test_build_rejects_non_exact_vcs_local_editable_or_direct_dependencies(
    tmp_path: Path,
    monkeypatch,
    unsafe_line: str,
) -> None:
    approved = _write_approved(tmp_path / "requirements-runtime.txt")
    freeze = _write_freeze(tmp_path / "runtime.freeze", extra=(unsafe_line,))
    monkeypatch.setattr(
        runtime_lock,
        "_download_one",
        lambda *_args: pytest.fail("freeze 门禁失败前不得下载制品"),
    )

    with pytest.raises(runtime_lock.RuntimeLockError, match="freeze"):
        runtime_lock.build_hashed_lock(
            freeze,
            approved,
            tmp_path / "runtime.lock",
            tmp_path / "artifacts",
        )


def test_build_removes_only_one_canonical_project_editable_record_without_leaking_it(
    tmp_path: Path,
    monkeypatch,
) -> None:
    approved = _write_approved(tmp_path / "requirements-runtime.txt")
    sensitive_marker = "private-user-info"
    project_record = (
        "-e git+https://"
        + sensitive_marker
        + "@example.invalid/codev-platform.git@main#egg=codev_platform"
    )
    freeze = _write_freeze(tmp_path / "runtime.freeze", extra=(project_record,))
    lock = tmp_path / "runtime.lock"
    monkeypatch.setattr(runtime_lock, "_download_one", _fake_downloader)

    runtime_lock.build_hashed_lock(freeze, approved, lock, tmp_path / "artifacts")

    content = lock.read_text(encoding="utf-8")
    assert sensitive_marker not in content
    assert "codev-platform==" not in content


def test_build_rejects_duplicate_project_editable_records(tmp_path: Path, monkeypatch) -> None:
    approved = _write_approved(tmp_path / "requirements-runtime.txt")
    record = "-e git+https://example.invalid/codev.git@main#egg=codev_platform"
    freeze = _write_freeze(tmp_path / "runtime.freeze", extra=(record, record))
    monkeypatch.setattr(runtime_lock, "_download_one", lambda *_args: None)

    with pytest.raises(runtime_lock.RuntimeLockError, match="项目可编辑记录"):
        runtime_lock.build_hashed_lock(
            freeze,
            approved,
            tmp_path / "runtime.lock",
            tmp_path / "artifacts",
        )


def test_build_requires_all_managed_distributions_before_download(
    tmp_path: Path, monkeypatch
) -> None:
    approved = _write_approved(tmp_path / "requirements-runtime.txt")
    freeze = _write_freeze(tmp_path / "runtime.freeze", changes={"mcp": None})
    monkeypatch.setattr(
        runtime_lock,
        "_download_one",
        lambda *_args: pytest.fail("必需包门禁失败前不得下载制品"),
    )

    with pytest.raises(runtime_lock.RuntimeLockError, match="必需发行包"):
        runtime_lock.build_hashed_lock(
            freeze,
            approved,
            tmp_path / "runtime.lock",
            tmp_path / "artifacts",
        )


def test_build_requires_torch_cuda_tag_to_match_index(tmp_path: Path, monkeypatch) -> None:
    approved = _write_approved(tmp_path / "requirements-runtime.txt")
    freeze = _write_freeze(
        tmp_path / "runtime.freeze",
        changes={"torch": "2.11.0+cu129"},
    )
    monkeypatch.setattr(
        runtime_lock,
        "_download_one",
        lambda *_args: pytest.fail("CUDA 门禁失败前不得下载制品"),
    )

    with pytest.raises(runtime_lock.RuntimeLockError, match="CUDA"):
        runtime_lock.build_hashed_lock(
            freeze,
            approved,
            tmp_path / "runtime.lock",
            tmp_path / "artifacts",
        )


def test_build_rejects_downloader_artifact_not_matching_pin(tmp_path: Path, monkeypatch) -> None:
    approved = _write_approved(tmp_path / "requirements-runtime.txt")
    freeze = _write_freeze(tmp_path / "runtime.freeze")

    def wrong_artifact(
        _requirement: str,
        download_dir: Path,
        _index: str,
        _primary: str,
        _cancellation=None,
    ) -> Path:
        artifact = download_dir / "other-1.0-py3-none-any.whl"
        artifact.write_bytes(b"wrong")
        return artifact

    monkeypatch.setattr(runtime_lock, "_download_one", wrong_artifact)

    with pytest.raises(runtime_lock.RuntimeLockError, match="wheel"):
        runtime_lock.build_hashed_lock(
            freeze,
            approved,
            tmp_path / "runtime.lock",
            tmp_path / "artifacts",
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda text: text.replace(" --hash=sha256:", " # removed-hash=", 1),
        lambda text: text + "unknown @ file:///tmp/unknown.whl\n",
        lambda text: text.replace("# codev-artifact ", "# codev-artifact duplicate ", 1),
    ],
)
def test_validator_rejects_malformed_or_unsafe_lock(
    tmp_path: Path,
    monkeypatch,
    mutation,
) -> None:
    lock, approved, _freeze = _build_valid_lock(tmp_path, monkeypatch)
    lock.write_text(mutation(lock.read_text(encoding="utf-8")), encoding="utf-8")

    with pytest.raises(runtime_lock.RuntimeLockError):
        runtime_lock.validate_requirements_lock(lock, approved)


def test_validator_requires_every_pin_to_have_artifact_hash(tmp_path: Path, monkeypatch) -> None:
    lock, approved, _freeze = _build_valid_lock(tmp_path, monkeypatch)
    lines = lock.read_text(encoding="utf-8").splitlines()
    lines = [line for line in lines if not line.startswith("# codev-artifact mcp ")]
    lock.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(runtime_lock.RuntimeLockError, match="制品"):
        runtime_lock.validate_requirements_lock(lock, approved)


def test_validate_cli_emits_only_structured_summary(tmp_path: Path, monkeypatch, capsys) -> None:
    lock, approved, _freeze = _build_valid_lock(tmp_path, monkeypatch)

    assert runtime_lock.main(["validate", str(lock), "--approved", str(approved)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "artifact_manifest_sha256": runtime_lock.validate_requirements_lock(
            lock, approved
        ).artifact_manifest_sha256,
        "cuda_tags": ["cu128"],
        "pin_count": len(_VERSIONS),
        "requirements_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
        "status": "ok",
    }
