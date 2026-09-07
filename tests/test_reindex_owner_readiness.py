"""只读 queue owner 就绪探针的契约。"""
from __future__ import annotations

import json

import pytest

import codev_platform.reindex.runtime_owner as runtime_owner
from codev_platform.reindex.owner_readiness import (
    OWNER_BOOTSTRAP_EXIT_CODE,
    OwnerReadiness,
    inspect_owner_readiness,
)
from codev_platform.reindex.runtime_owner import (
    QueueBackendBinding,
    QueueOwnerUnavailableError,
    fingerprint_backend,
    read_queue_owner_file,
)


def _binding(locator: str = "C:/private/reindex-queue") -> QueueBackendBinding:
    return QueueBackendBinding("file", fingerprint_backend("file", locator))


def _write_owner(path, binding: QueueBackendBinding) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "owner_token": "a" * 32,
                "backend_kind": binding.kind,
                "backend_fingerprint": binding.fingerprint,
                "created_at": 1.0,
            }
        ),
        encoding="utf-8",
    )


def _异常链文本(error: BaseException) -> str:
    pending = [error]
    seen: set[int] = set()
    values: list[str] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        values.extend((str(current), repr(current)))
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return "\n".join(values)


def test_缺失owner探针不创建默认父目录或锁文件(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(data_dir))
    path = (data_dir / "run" / "reindex-queue-owner.json").resolve()

    report = inspect_owner_readiness(_binding())

    assert report.status is OwnerReadiness.BOOTSTRAP_REQUIRED
    assert not path.parent.exists()
    assert not path.with_name(f"{path.name}.lock").exists()
    assert OWNER_BOOTSTRAP_EXIT_CODE == 77


def test_匹配owner返回就绪且报告不含token(tmp_path) -> None:
    path = (tmp_path / "reindex-queue-owner.json").resolve()
    binding = _binding()
    _write_owner(path, binding)

    report = inspect_owner_readiness(binding, path=path)

    assert report.status is OwnerReadiness.READY
    assert "a" * 32 not in repr(report)


def test_损坏owner返回恢复必需(tmp_path) -> None:
    path = (tmp_path / "reindex-queue-owner.json").resolve()
    path.write_text('{"schema_version": 1}', encoding="utf-8")

    report = inspect_owner_readiness(_binding(), path=path)

    assert report.status is OwnerReadiness.RECOVERY_REQUIRED


@pytest.mark.parametrize("schema_version", [True, 1.0])
def test_owner_schema版本必须为精确整数(tmp_path, schema_version) -> None:
    path = (tmp_path / "reindex-queue-owner.json").resolve()
    _write_owner(path, _binding())
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = schema_version
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = inspect_owner_readiness(_binding(), path=path)

    assert report.status is OwnerReadiness.RECOVERY_REQUIRED


def test_owner绑定不匹配返回绑定不匹配(tmp_path) -> None:
    path = (tmp_path / "reindex-queue-owner.json").resolve()
    _write_owner(path, _binding("C:/private/old-queue"))

    report = inspect_owner_readiness(_binding("C:/private/new-queue"), path=path)

    assert report.status is OwnerReadiness.BINDING_MISMATCH


def test_读取不可用返回不可用且不泄露异常内容(tmp_path, monkeypatch) -> None:
    path = (tmp_path / "reindex-queue-owner.json").resolve()
    secret = "a" * 32

    def _raise_unavailable(*_args, **_kwargs):
        raise PermissionError(f"owner={secret}")

    monkeypatch.setattr(runtime_owner, "read_regular_file_bounded", _raise_unavailable)

    report = inspect_owner_readiness(_binding(), path=path)

    assert report.status is OwnerReadiness.UNAVAILABLE
    assert secret not in repr(report)


def test_纯读取函数把不可用读取转换为安全错误(tmp_path, monkeypatch) -> None:
    path = (tmp_path / "reindex-queue-owner.json").resolve()
    secret = "a" * 32

    def _raise_unavailable(*_args, **_kwargs):
        raise OSError(f"owner={secret}")

    monkeypatch.setattr(runtime_owner, "read_regular_file_bounded", _raise_unavailable)

    with pytest.raises(QueueOwnerUnavailableError) as raised:
        read_queue_owner_file(path)
    assert secret not in _异常链文本(raised.value)


@pytest.mark.parametrize("error_type", [MemoryError, KeyboardInterrupt, SystemExit])
def test_终止性读取异常必须原样传播(tmp_path, monkeypatch, error_type) -> None:
    path = (tmp_path / "reindex-queue-owner.json").resolve()

    def _raise_termination(*_args, **_kwargs):
        raise error_type()

    monkeypatch.setattr(runtime_owner, "read_regular_file_bounded", _raise_termination)

    with pytest.raises(error_type):
        inspect_owner_readiness(_binding(), path=path)
