"""attempt artifact 固定路径、严格加载与幂等清理测试。"""
from __future__ import annotations

import dataclasses
import hashlib
import os
import stat
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform.reindex import attempt_artifacts as artifacts_module
from codev_platform.reindex.attempt_artifacts import FilesystemAttemptArtifactStore
from codev_platform.reindex.attempt_completion import (
    make_attempt_completion_receipt,
    write_attempt_completion_receipt,
)
from codev_platform.reindex.attempts import (
    AttemptJournalEntry,
    AttemptOutcome,
    AttemptResult,
    AttemptSpec,
    CanonicalJsonObject,
    write_attempt_result_atomic,
)

_OID = "a" * 40
_RUNTIME = "b" * 64


def _spec(**changes: object) -> AttemptSpec:
    value = AttemptSpec(
        schema_version=1,
        attempt_id="attempt-1",
        fence="fence-1",
        project_id="demo",
        kind="all",
        input_kind="configured",
        input_payload=CanonicalJsonObject.from_value({"project_id": "demo"}),
        target_commit=_OID,
        timeout_sec=30.0,
        runtime_revision=_RUNTIME,
    )
    return dataclasses.replace(value, **changes) if changes else value


def _result(**changes: object) -> AttemptResult:
    value = AttemptResult(
        schema_version=1,
        attempt_id="attempt-1",
        fence="fence-1",
        project_id="demo",
        kind="all",
        input_root="/work/input",
        target_commit=_OID,
        input_commits=(("primary", _OID),),
        input_trees=(("primary", "c" * 40),),
        runtime_revision=_RUNTIME,
        outcome=AttemptOutcome.SUCCEEDED,
        rc=0,
        retryable=False,
        note="",
        timing=(("started_at", 10.0), ("finished_at", 12.0)),
        log_ref=None,
        proof=CanonicalJsonObject.from_value({"success": True}),
    )
    return dataclasses.replace(value, **changes) if changes else value


def _claim() -> SimpleNamespace:
    return SimpleNamespace(
        claim_token="claim-secret",
        job=SimpleNamespace(
            project_id="demo",
            kind="all",
            meta=SimpleNamespace(target_commit=_OID),
        ),
    )


def _journal(paths, **changes: object) -> AttemptJournalEntry:
    value = AttemptJournalEntry(
        schema_version=1,
        owner_token="owner-1",
        claim_token="claim-secret",
        attempt_id="attempt-1",
        fence="fence-1",
        project_id="demo",
        kind="all",
        spec_path=str(paths.spec),
        result_path=str(paths.result),
        pid=123,
        process_identity="identity-1",
        containment_kind="windows-job",
        native_ref="job-1",
        state="executing",
        started_at=10.0,
        timeout_sec=30.0,
    )
    return dataclasses.replace(value, **changes) if changes else value


def _completed(store: FilesystemAttemptArtifactStore):
    spec = _spec()
    result = _result()
    paths = store.initialize(spec)
    store.write_result_once(paths, result)
    receipt = make_attempt_completion_receipt(
        spec,
        result,
        process_rc=0,
        observed_at=13.0,
    )
    write_attempt_completion_receipt(paths.receipt, receipt)
    return paths, spec, result, receipt


def test_artifact_路径只由_attempt_id_小写摘要和固定文件名组成(tmp_path: Path) -> None:
    store = FilesystemAttemptArtifactStore(tmp_path.resolve())

    paths = store.expected("attempt-1")

    digest = hashlib.sha256(b"attempt-1").hexdigest()
    assert paths.root == tmp_path.resolve() / digest
    assert paths.spec == paths.root / "spec.json"
    assert paths.result == paths.root / "result.json"
    assert paths.receipt == paths.root / "completion.json"
    assert paths.bootstrap_log == paths.root / "bootstrap.log"
    with pytest.raises(ValueError):
        store.expected(" attempt-1 ")


@pytest.mark.skipif(os.name != "nt", reason="Windows 设备命名空间测试")
@pytest.mark.parametrize("root", [r"\\.\C:\artifacts", r"\\?\C:\artifacts"])
def test_windows_构造根在任何创建前拒绝设备命名空间(root: str) -> None:
    with pytest.raises(ValueError, match="设备命名空间"):
        FilesystemAttemptArtifactStore(Path(root))


def test_构造根拒绝既有_symlink_或_reparse_point(tmp_path: Path) -> None:
    real = tmp_path / "real-artifacts"
    link = tmp_path / "linked-artifacts"
    real.mkdir()
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"当前环境不能创建目录链接：{exc}")

    with pytest.raises(ValueError, match="链接|reparse"):
        FilesystemAttemptArtifactStore(link.absolute())


def test_initialize_拒绝预置的_attempt_目录链接(tmp_path: Path) -> None:
    store = FilesystemAttemptArtifactStore(tmp_path.resolve())
    paths = store.expected("attempt-1")
    outside = tmp_path / "outside-attempt"
    outside.mkdir()
    try:
        paths.root.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"当前环境不能创建目录链接：{exc}")

    with pytest.raises(ValueError, match="链接|reparse"):
        store.initialize(_spec())

    assert list(outside.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX 权限位测试")
def test_initialize_在任意_umask_下创建_0700_目录和_0600_文件(tmp_path: Path) -> None:
    store = FilesystemAttemptArtifactStore(tmp_path.resolve())
    previous = os.umask(0)
    try:
        paths = store.initialize(_spec())
        store.write_result_once(paths, _result())
        write_attempt_completion_receipt(
            paths.receipt,
            make_attempt_completion_receipt(
                _spec(),
                _result(),
                process_rc=0,
                observed_at=13.0,
            ),
        )
    finally:
        os.umask(previous)

    assert stat.S_IMODE(paths.root.stat().st_mode) == 0o700
    for path in (paths.spec, paths.result, paths.receipt, paths.bootstrap_log):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert paths.bootstrap_log.read_bytes() == b""


def test_initialize_是_create_once_且不接管既有目录(tmp_path: Path) -> None:
    store = FilesystemAttemptArtifactStore(tmp_path.resolve())
    paths = store.initialize(_spec())

    with pytest.raises(FileExistsError):
        store.initialize(_spec())

    assert store.read_spec(paths) == _spec()


def test_initialize_拒绝尚未清理的_deterministic_tombstone(tmp_path: Path) -> None:
    store = FilesystemAttemptArtifactStore(tmp_path.resolve())
    paths = store.expected("attempt-1")
    tombstone = paths.root.with_name(f"{paths.root.name}.cleanup")
    tombstone.mkdir()

    with pytest.raises(ValueError, match="cleanup|清理"):
        store.initialize(_spec())

    assert paths.root.exists() is False
    assert tombstone.is_dir()


@pytest.mark.skipif(os.name != "nt", reason="Windows 原子目录 ACL 测试")
def test_windows_attempt_目录必须在创建时原子附加_owner_only_acl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = (tmp_path / "artifacts").resolve()
    base.mkdir()
    store = FilesystemAttemptArtifactStore(base)
    digest = hashlib.sha256(b"attempt-1").hexdigest()
    real_mkdir = os.mkdir

    def _mkdir(path: str | bytes | Path, mode: int = 0o777) -> None:
        if Path(path).name == digest and not Path(path).exists():
            raise AssertionError("attempt 目录不能先继承 ACL 再事后收紧")
        real_mkdir(path, mode)

    monkeypatch.setattr(artifacts_module.os, "mkdir", _mkdir)

    paths = store.initialize(_spec())

    assert paths.root.is_dir()


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL 故障注入")
def test_windows_attempt_目录_acl_构造失败时不创建目录(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FilesystemAttemptArtifactStore(tmp_path.resolve())
    paths = store.expected("attempt-1")

    def _fail_sid() -> str:
        raise PermissionError("注入目录 ACL 构造失败")

    monkeypatch.setattr(artifacts_module, "_current_windows_sid", _fail_sid)

    with pytest.raises(PermissionError, match="ACL 构造失败"):
        store.initialize(_spec())

    assert paths.root.exists() is False


def test_load_validated_交叉校验三件套并使用_receipt_时间(tmp_path: Path) -> None:
    store = FilesystemAttemptArtifactStore(tmp_path.resolve())
    paths, spec, result, receipt = _completed(store)

    validated = store.load_validated(paths, _claim())

    assert validated.spec == spec
    assert validated.result == result
    assert validated.process_rc == receipt.process_rc
    assert validated.validated_at == receipt.observed_at == 13.0


def test_load_validated_拒绝_spec_result_receipt_身份错配(tmp_path: Path) -> None:
    store = FilesystemAttemptArtifactStore(tmp_path.resolve())
    spec = _spec()
    paths = store.initialize(spec)
    other_spec = _spec(fence="fence-other")
    other_result = _result(fence="fence-other")
    write_attempt_result_atomic(paths.result, other_result)
    write_attempt_completion_receipt(
        paths.receipt,
        make_attempt_completion_receipt(
            other_spec,
            other_result,
            process_rc=0,
            observed_at=13.0,
        ),
    )

    with pytest.raises(ValueError, match="身份|不匹配"):
        store.load_validated(paths, _claim())


def test_missing_result_和_receipt_只返回_none_而损坏文件失败关闭(tmp_path: Path) -> None:
    store = FilesystemAttemptArtifactStore(tmp_path.resolve())
    paths = store.initialize(_spec())

    assert store.read_result(paths) is None
    assert store.read_receipt(paths) is None
    paths.result.write_bytes(b"not-json")
    with pytest.raises(ValueError):
        store.read_result(paths)


def test_verify_journal_拒绝任意路径与固定文件链接(tmp_path: Path) -> None:
    store = FilesystemAttemptArtifactStore(tmp_path.resolve())
    paths = store.initialize(_spec())
    entry = _journal(paths)

    assert store.verify_journal(entry) == paths
    with pytest.raises(ValueError, match="路径"):
        store.verify_journal(dataclasses.replace(entry, spec_path=str(tmp_path / "other")))

    outside = tmp_path / "outside-result.json"
    outside.write_bytes(b"outside")
    try:
        paths.result.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"当前环境不能创建链接：{exc}")
    with pytest.raises(ValueError, match="普通文件|链接|reparse"):
        store.verify_journal(entry)
    assert outside.read_bytes() == b"outside"


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO 行为测试")
def test_read_result_拒绝_fifo_且不等待写端(tmp_path: Path) -> None:
    store = FilesystemAttemptArtifactStore(tmp_path.resolve())
    paths = store.initialize(_spec())
    os.mkfifo(paths.result)

    started = time.monotonic()
    with pytest.raises(ValueError, match="普通文件"):
        store.read_result(paths)
    assert time.monotonic() - started < 1.0


def test_cleanup_拒绝额外条目且不删除受管目录(tmp_path: Path) -> None:
    store = FilesystemAttemptArtifactStore(tmp_path.resolve())
    paths = store.initialize(_spec())
    extra = paths.root / "foreign.txt"
    extra.write_text("foreign", encoding="utf-8")

    with pytest.raises(ValueError, match="额外"):
        store.cleanup(paths)

    assert paths.root.is_dir()
    assert extra.read_text(encoding="utf-8") == "foreign"


def test_cleanup_先隐藏固定目录_可重入且不处理外层_journal(tmp_path: Path) -> None:
    store = FilesystemAttemptArtifactStore(tmp_path.resolve())
    paths, _spec_value, _result_value, _receipt_value = _completed(store)
    journal = tmp_path / "attempt-journal.json"
    journal.write_text("keep-until-outer-clears", encoding="utf-8")

    store.cleanup(paths)
    store.cleanup(paths)

    assert paths.root.exists() is False
    assert journal.read_text(encoding="utf-8") == "keep-until-outer-clears"
    assert (tmp_path / f"{paths.root.name}.cleanup").exists() is False


def test_cleanup_可恢复已经改名的固定_tombstone(tmp_path: Path) -> None:
    store = FilesystemAttemptArtifactStore(tmp_path.resolve())
    paths = store.initialize(_spec())
    tombstone = paths.root.with_name(f"{paths.root.name}.cleanup")
    os.replace(paths.root, tombstone)

    store.cleanup(paths)

    assert paths.root.exists() is False
    assert tombstone.exists() is False


def test_cleanup_可恢复_durable_unlink_留下的自有文件_tombstone(tmp_path: Path) -> None:
    store = FilesystemAttemptArtifactStore(tmp_path.resolve())
    paths = store.initialize(_spec())
    tombstone = paths.root.with_name(f"{paths.root.name}.cleanup")
    os.replace(paths.root, tombstone)
    owned = tombstone / f".spec.json.{'f' * 32}.deleted"
    os.replace(tombstone / "spec.json", owned)

    store.cleanup(paths)

    assert tombstone.exists() is False


def test_cleanup_发现_root_与_tombstone_同时存在时失败关闭(tmp_path: Path) -> None:
    store = FilesystemAttemptArtifactStore(tmp_path.resolve())
    paths = store.initialize(_spec())
    tombstone = paths.root.with_name(f"{paths.root.name}.cleanup")
    tombstone.mkdir()

    with pytest.raises(ValueError, match="同时存在|分叉"):
        store.cleanup(paths)

    assert paths.root.is_dir()
    assert tombstone.is_dir()
