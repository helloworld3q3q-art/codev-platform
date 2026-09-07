"""隔离 worker 跨重启 queue owner 的耐久与失败关闭契约。"""
from __future__ import annotations

import json

import pytest

from codev_platform.reindex.runtime_owner import (
    QueueBackendBinding,
    QueueOwnerBootstrapRequired,
    QueueOwnerIdentity,
    QueueOwnerRecoveryRequired,
    QueueOwnerStore,
    fingerprint_backend,
)


def _binding(kind: str, locator: str) -> QueueBackendBinding:
    return QueueBackendBinding(kind, fingerprint_backend(kind, locator))


def _store(tmp_path) -> QueueOwnerStore:
    return QueueOwnerStore(path=(tmp_path / "reindex-queue-owner.json").resolve())


def _exception_chain_text(error: BaseException) -> str:
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


def test_常规启动只读取已初始化owner而不擅自创建(tmp_path) -> None:
    binding = _binding("file", "C:/private/reindex_queue")

    with pytest.raises(QueueOwnerBootstrapRequired, match="初始化|owner"):
        _store(tmp_path).load(binding)

    assert not (tmp_path / "reindex-queue-owner.json").exists()


def test_显式初始化后跨重启保持同一queue_owner且不写入原始定位信息(tmp_path) -> None:
    binding = _binding("file", "C:/private/reindex_queue")
    first = _store(tmp_path).initialize(binding, recovery_state_present=lambda _timeout: False)
    restarted = _store(tmp_path).load(binding)
    raw = (tmp_path / "reindex-queue-owner.json").read_text(encoding="utf-8")

    assert restarted == first
    assert len(first.token) == 32
    assert "private/reindex_queue" not in raw
    assert json.loads(raw)["backend_fingerprint"] == binding.fingerprint


def test_queue_owner_identity的repr不泄露稳定token() -> None:
    owner = QueueOwnerIdentity("a" * 32, _binding("file", "C:/spool"), 1.0)

    assert "a" * 32 not in repr(owner)


def test_owner文件缺失但存在待恢复状态时失败关闭且不创建新身份(tmp_path) -> None:
    store = _store(tmp_path)

    with pytest.raises(QueueOwnerRecoveryRequired, match="缺失|恢复"):
        store.initialize(_binding("file", "C:/spool"), recovery_state_present=lambda _timeout: True)

    assert not (tmp_path / "reindex-queue-owner.json").exists()


def test_owner文件损坏但存在待恢复状态时保留原文件并失败关闭(tmp_path) -> None:
    path = tmp_path / "reindex-queue-owner.json"
    path.write_text('{"schema_version":1,"owner_token":"bad"}', encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(QueueOwnerRecoveryRequired, match="损坏|owner"):
        _store(tmp_path).initialize(
            _binding("file", "C:/spool"),
            recovery_state_present=lambda _timeout: False,
        )

    assert path.read_bytes() == before


def test_backend变更时即使探针为空也拒绝静默轮换owner(tmp_path) -> None:
    store = _store(tmp_path)
    original = store.initialize(
        _binding("file", "C:/spool-a"),
        recovery_state_present=lambda _timeout: False,
    )

    with pytest.raises(QueueOwnerRecoveryRequired, match="后端|绑定"):
        store.initialize(
            _binding("pg", "postgresql://user:secret@host/db"),
            recovery_state_present=lambda _timeout: False,
        )

    assert _store(tmp_path).load(
        _binding("file", "C:/spool-a"),
    ) == original


def test_仅缺失owner且显式初始化并通过有界探针才允许写入(tmp_path) -> None:
    store = _store(tmp_path)
    budgets: list[float] = []

    owner = store.initialize(
        _binding("file", "C:/spool-a"),
        recovery_state_present=lambda timeout: budgets.append(timeout) or False,
    )

    assert owner.binding.kind == "file"
    assert len(budgets) == 1
    assert budgets[0] > 0


def test_owner文件解析和恢复探针异常链不泄露token能力(tmp_path) -> None:
    token = "a" * 32
    path = tmp_path / "reindex-queue-owner.json"
    path.write_bytes(b"\xff" + token.encode())
    store = _store(tmp_path)

    with pytest.raises(QueueOwnerRecoveryRequired) as decoded:
        store.load(_binding("file", "C:/spool"))
    assert token not in _exception_chain_text(decoded.value)

    second = tmp_path / "second"
    second.mkdir()
    with pytest.raises(QueueOwnerRecoveryRequired) as callback:
        _store(second).initialize(
            _binding("file", "C:/spool"),
            recovery_state_present=lambda _timeout: (_ for _ in ()).throw(RuntimeError(token)),
        )
    assert token not in _exception_chain_text(callback.value)
