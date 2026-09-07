"""pending-CAS 严格语义在 File 与 Pg 后端的对称测试。"""
from __future__ import annotations

import json
import os

import pytest

from codev_platform.reindex.file_queue import FileSpoolQueue
from codev_platform.reindex.pg_queue import PgJobQueue
from codev_platform.reindex.pg_queue_codec import pending_version_from_row
from codev_platform.reindex.pg_queue_migration import PgPendingMigrationError
from codev_platform.reindex.queue_ports import Job, JobMeta, PendingMigrationOutcome
from tests.pg_queue_fakes import FakeCursor, unit_queue


def _待办行(
    *,
    enqueued_at: float = 1.0,
    token: str = "pending-1",
    metadata: str = '{"source":"legacy","pull_policy":null,"target_commit":"HEAD"}',
    claim: str | None = None,
    owner: str | None = None,
) -> tuple[object, ...]:
    return (
        enqueued_at,
        enqueued_at,
        metadata,
        "pending",
        claim,
        owner,
        token,
        None,
        "101",
    )


def _待办快照(*, version: str = "pt:pending-1") -> Job:
    return Job(
        "demo",
        "chroma",
        1.0,
        meta=JobMeta(target_commit="HEAD"),
        pending_version=version,
    )


def _历史待办迁移行(
    *,
    pending_enqueued_at: object = None,
    pending_metadata: object = None,
    status: object = "pending",
    claim: object = None,
    owner: object = None,
    pending_token: object = None,
    quarantine: object = None,
    xmin: object = "101",
) -> tuple[object, ...]:
    return (
        1.0,
        pending_enqueued_at,
        pending_metadata,
        status,
        claim,
        owner,
        pending_token,
        quarantine,
        xmin,
    )


def _扫描待办行(
    *,
    pending_enqueued_at: object = None,
    pending_metadata: object = None,
    status: object = "pending",
    claim: object = None,
    owner: object = None,
    pending_token: object = None,
    quarantine: object = None,
    xmin: object = "101",
    active_metadata: object = None,
    result_metadata: object = None,
) -> tuple[object, ...]:
    row: list[object] = [None] * 24
    row[0] = "demo"
    row[1] = "chroma"
    row[2] = 1.0
    row[3] = pending_enqueued_at
    row[4] = pending_metadata
    row[5] = status
    row[6] = 2.0 if active_metadata is not None else None
    row[7] = active_metadata
    row[8] = claim
    row[9] = 100.0 if active_metadata is not None else None
    row[10] = "completed" if result_metadata is not None else None
    row[11] = 3.0 if result_metadata is not None else None
    row[12] = result_metadata
    row[13] = 1.0
    row[14] = owner
    row[21] = quarantine
    row[22] = pending_token
    row[23] = xmin
    return tuple(row)


def test_File旧sidecar原样重写后拒绝旧指纹(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    queue.enqueue("demo", "chroma", JobMeta(target_commit="HEAD"))
    path = queue.location / "pending" / "demo__chroma.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("pending_token")
    path.write_text(json.dumps(payload), encoding="utf-8")
    expected = queue.snapshot().pending[0]
    original = path.read_bytes()
    stat = path.stat()
    path.write_bytes(original)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    result = queue.migrate_pending(
        expected,
        JobMeta(target_commit="a" * 40),
        timeout_sec=0.3,
    )

    assert result.outcome is PendingMigrationOutcome.VERSION_CONFLICT
    assert path.read_bytes() == original


def test_File损坏pending_token不提供可迁移版本(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    queue.enqueue("demo", "chroma", JobMeta(target_commit="HEAD"))
    path = queue.location / "pending" / "demo__chroma.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["pending_token"] = ""
    path.write_text(json.dumps(payload), encoding="utf-8")

    pending = queue.snapshot().pending[0]

    assert pending.pending_version is None


@pytest.mark.parametrize("字段", ["claim_token", "owner_token"])
def test_File当前pending含claim或owner时失败关闭且不改写(tmp_path, 字段: str) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    queue.enqueue("demo", "chroma", JobMeta(target_commit="HEAD"))
    expected = queue.snapshot().pending[0]
    path = queue.location / "pending" / "demo__chroma.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[字段] = "unexpected-state"
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = path.read_bytes()

    result = queue.migrate_pending(
        expected,
        JobMeta(target_commit="a" * 40),
        timeout_sec=0.3,
    )

    assert result.outcome is PendingMigrationOutcome.NOT_PENDING
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("字段", "值"),
    [
        ("meta", {"source": "migration", "pull_policy": "never", "target_commit": "a" * 40}),
        ("enqueued_at", 1.0),
    ],
)
def test_File同token但完整pending事实变化时拒绝更新(
    tmp_path,
    字段: str,
    值: object,
) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    queue.enqueue("demo", "chroma", JobMeta(target_commit="HEAD"))
    expected = queue.snapshot().pending[0]
    path = queue.location / "pending" / "demo__chroma.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[字段] = 值
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = path.read_bytes()

    result = queue.migrate_pending(
        expected,
        JobMeta(target_commit="b" * 40),
        timeout_sec=0.3,
    )

    assert result.outcome is PendingMigrationOutcome.VERSION_CONFLICT
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("字段", "值"),
    [("claim_token", 0), ("owner_token", {})],
)
def test_File当前原始claim或owner类型异常时失败关闭且不改写(
    tmp_path,
    字段: str,
    值: object,
) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    queue.enqueue("demo", "chroma", JobMeta(target_commit="HEAD"))
    expected = queue.snapshot().pending[0]
    path = queue.location / "pending" / "demo__chroma.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[字段] = 值
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = path.read_bytes()

    result = queue.migrate_pending(
        expected,
        JobMeta(target_commit="a" * 40),
        timeout_sec=0.3,
    )

    assert result.outcome is PendingMigrationOutcome.NOT_PENDING
    assert path.read_bytes() == before


def test_File损坏v2元数据时失败关闭且不改写(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    queue.enqueue("demo", "chroma", JobMeta(target_commit="HEAD"))
    expected = queue.snapshot().pending[0]
    path = queue.location / "pending" / "demo__chroma.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["meta"] = {"source": 0}
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = path.read_bytes()

    result = queue.migrate_pending(
        expected,
        JobMeta(target_commit="a" * 40),
        timeout_sec=0.3,
    )

    assert result.outcome is PendingMigrationOutcome.NOT_PENDING
    assert path.read_bytes() == before


def test_File同时存在active和result时只迁移精确pending(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue", owner_token="worker-a")
    queue.enqueue("demo", "chroma", JobMeta(target_commit="HEAD"))
    claim = queue.claim(
        owner_token="worker-a",
        projects=None,
        limit=1,
        timeout_sec=0.3,
    )[0]
    queue.enqueue("demo", "chroma", JobMeta(target_commit="HEAD"))
    result_path = queue.location / "results" / "demo__chroma.json"
    queue._store.write_json_atomic(result_path, {
        "project_id": claim.job.project_id,
        "kind": claim.job.kind,
        "enqueued_at": claim.job.enqueued_at,
        "meta": {"source": "legacy", "pull_policy": None, "target_commit": "HEAD"},
        "result_status": "completed",
        "finished_at": 1.0,
    })
    active_path = queue.location / "active" / "demo__chroma.json"
    active_before = active_path.read_bytes()
    result_before = result_path.read_bytes()
    expected = queue.snapshot().pending[0]

    result = queue.migrate_pending(
        expected,
        JobMeta(target_commit="b" * 40),
        timeout_sec=0.3,
    )

    assert result.outcome is PendingMigrationOutcome.MIGRATED
    assert active_path.read_bytes() == active_before
    assert result_path.read_bytes() == result_before


def test_File迁移不搬运调用后出现的历史marker(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    queue.enqueue("demo", "chroma", JobMeta(target_commit="HEAD"))
    expected = queue.snapshot().pending[0]
    marker = queue.location / "demo__chroma"
    marker.write_text("", encoding="utf-8")

    result = queue.migrate_pending(
        expected,
        JobMeta(target_commit="c" * 40),
        timeout_sec=0.3,
    )

    assert result.outcome is PendingMigrationOutcome.MIGRATED
    assert marker.exists()


def test_Pg版本冲突不执行更新() -> None:
    queue = unit_queue(FakeCursor(rows=[_待办行(token="pending-new")]))

    result = queue.migrate_pending(
        _待办快照(version="pt:pending-old"),
        JobMeta(target_commit="a" * 40),
        timeout_sec=0.3,
    )

    assert result.outcome is PendingMigrationOutcome.VERSION_CONFLICT
    assert len(queue._pool._conn.calls) == 1


@pytest.mark.parametrize(
    ("当前入队时间", "当前元数据"),
    [
        (1.0, '{"source":"migration","pull_policy":"never","target_commit":"' + "a" * 40 + '"}'),
        (2.0, '{"source":"legacy","pull_policy":null,"target_commit":"HEAD"}'),
    ],
)
def test_Pg同token但完整pending事实变化时拒绝更新(
    当前入队时间: float,
    当前元数据: str,
) -> None:
    queue = unit_queue(
        FakeCursor(rows=[_待办行(enqueued_at=当前入队时间, metadata=当前元数据)]),
        FakeCursor(rowcount=1),
    )

    result = queue.migrate_pending(
        _待办快照(),
        JobMeta(target_commit="a" * 40),
        timeout_sec=0.3,
    )

    assert result.outcome is PendingMigrationOutcome.VERSION_CONFLICT
    assert len(queue._pool._conn.calls) == 1


def test_Pg损坏pending_token不提供迁移版本() -> None:
    row: list[object] = [None] * 24
    row[3] = 1.0
    row[22] = ""
    row[23] = "101"

    assert pending_version_from_row(tuple(row)) is None


def test_Pg历史待办带owner时不执行xmin更新() -> None:
    row = (
        1.0,
        None,
        None,
        "pending",
        None,
        "leftover-owner",
        None,
        None,
        "101",
    )
    queue = unit_queue(FakeCursor(rows=[row]), FakeCursor(rowcount=1))

    result = queue.migrate_pending(
        _待办快照(version="px:101"),
        JobMeta(target_commit="a" * 40),
        timeout_sec=0.3,
    )

    assert result.outcome is PendingMigrationOutcome.NOT_PENDING
    assert len(queue._pool._conn.calls) == 1


@pytest.mark.parametrize(
    ("名称", "字段", "预期版本"),
    [
        ("待办元数据", {"pending_metadata": "{}"}, "px:101"),
        ("待办token", {"pending_token": "pending-token"}, "pt:pending-token"),
        ("非待办状态", {"status": "running"}, "px:101"),
        ("claim", {"claim": "claim-1"}, "px:101"),
        ("owner", {"owner": "owner-1"}, "px:101"),
        ("隔离", {"quarantine": 1.0}, "px:101"),
        ("缺失xmin", {"xmin": None}, "px:101"),
        ("非法xmin", {"xmin": "not-an-xmin"}, "px:not-an-xmin"),
    ],
)
def test_Pg非规范历史待办既不暴露xmin也不更新(
    名称: str,
    字段: dict[str, object],
    预期版本: str,
) -> None:
    scan = _扫描待办行(**字段)
    queue = unit_queue(
        FakeCursor(rows=[_历史待办迁移行(**字段)]),
        FakeCursor(rowcount=1),
    )

    result = queue.migrate_pending(
        _待办快照(version=预期版本),
        JobMeta(target_commit="a" * 40),
        timeout_sec=0.3,
    )

    assert (
        pending_version_from_row(scan),
        result.outcome,
        len(queue._pool._conn.calls),
    ) == (None, PendingMigrationOutcome.NOT_PENDING, 1), 名称


def test_Pg有效v2待办与active结果共存时只更新pending字段() -> None:
    metadata = '{"source":"legacy","pull_policy":null,"target_commit":"HEAD"}'
    scan = _扫描待办行(
        pending_enqueued_at=1.0,
        pending_metadata=metadata,
        pending_token="pending-1",
        claim="active-claim",
        owner="active-owner",
        active_metadata=metadata,
        result_metadata=metadata,
    )
    expected = PgJobQueue._pending_job(scan)
    assert expected is not None
    queue = unit_queue(
        FakeCursor(rows=[_待办行(metadata=metadata, claim="active-claim", owner="active-owner")]),
        FakeCursor(rowcount=1),
    )

    result = queue.migrate_pending(
        expected,
        JobMeta(target_commit="a" * 40),
        timeout_sec=0.3,
    )

    assert result.outcome is PendingMigrationOutcome.MIGRATED
    set_clause = queue._pool._conn.calls[-1][0].split("WHERE", 1)[0]
    assert "pending_" in set_clause
    assert "active_" not in set_clause
    assert "result_" not in set_clause
    assert "claimed_by" not in set_clause


def test_Pg锁超时归一为忙碌结果() -> None:
    class 锁不可用(Exception):
        sqlstate = "55P03"

    queue = unit_queue(锁不可用("postgresql://user:secret@db.example/reindex"))

    result = queue.migrate_pending(
        _待办快照(),
        JobMeta(target_commit="a" * 40),
        timeout_sec=0.3,
    )

    assert result.outcome is PendingMigrationOutcome.BUSY


def test_Pg后端异常不泄露连接字符串() -> None:
    secret_dsn = "postgresql://user:top-secret@db.example/reindex"
    queue = unit_queue(RuntimeError(f"查询失败: {secret_dsn}"))

    with pytest.raises(PgPendingMigrationError) as captured:
        queue.migrate_pending(
            _待办快照(),
            JobMeta(target_commit="a" * 40),
            timeout_sec=0.3,
        )

    assert "top-secret" not in str(captured.value)
    assert captured.value.__cause__ is None


def test_Pg损坏待办元数据时绝不越权更新() -> None:
    queue = unit_queue(
        FakeCursor(rows=[_待办行(metadata="{损坏")]),
        FakeCursor(rowcount=1),
    )

    result = queue.migrate_pending(
        _待办快照(),
        JobMeta(target_commit="a" * 40),
        timeout_sec=0.3,
    )

    assert result.outcome is PendingMigrationOutcome.NOT_PENDING
    assert len(queue._pool._conn.calls) == 1
