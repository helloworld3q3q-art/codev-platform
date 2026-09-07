# Task 3 Report: Queue Protocol V2 Data Model

## Scope

- Added `JobMeta` and `QueueSnapshot` to `codev_platform/reindex/queue.py`
- Expanded `Job` with legacy-compatible metadata defaults
- Updated `FileSpoolQueue` to persist basic metadata in existing pending marker files without introducing Task 4 phase directories
- Exported the new queue model types from `codev_platform/reindex/__init__.py`
- Added focused regression coverage in `tests/test_reindex_queue.py`
- Did not implement Task 4 pending/active/results directories, lease operations, or PG queue changes

## TDD Log

### RED

Command:

```powershell
python -m pytest tests/test_reindex_queue.py
```

Key output:

```text
E   ImportError: cannot import name 'JobMeta' from 'codev_platform.reindex.queue'
```

Result: failed for the expected reason before production changes; the v2 queue model types did not exist yet.

### GREEN

Command:

```powershell
python -m pytest tests/test_reindex_queue.py tests/test_reindex_queue_cli.py
```

Key output:

```text
collected 28 items
28 passed in 0.29s
```

Result: task-focused queue protocol and CLI tests passed after adding the v2 data model and FileSpool metadata round-trip support.

## Implementation Notes

- `JobMeta` defaults to `source="legacy"` so existing `Job(project_id, kind, enqueued_at)` construction stays valid.
- `FileSpoolQueue.enqueue(..., meta=...)` now writes compact JSON metadata into the existing marker file and still uses file `mtime` as the pending timestamp.
- Old empty FileSpool marker files remain readable; missing or invalid marker content falls back to legacy metadata.
- `snapshot()` is now available on `FileSpoolQueue` and returns a stable `QueueSnapshot` shape with `pending` populated and `active/results` empty in Task 3.

## Files Changed

- `codev_platform/reindex/queue.py`
- `codev_platform/reindex/__init__.py`
- `tests/test_reindex_queue.py`
- `docs/plans/roadmap-2026-07-09/reindex-worker-v2-task-3-report-2026-07-14.md`

## Residual Concerns

- `PgJobQueue` still exposes the v1 surface at runtime; Task 3 intentionally left it unchanged per brief, so `snapshot()` is only implemented on `FileSpoolQueue` for now.

---

## Task 3 Fix: PG Queue v2 Surface Compatibility

### Scope

- Extended `PgJobQueue.enqueue()` to accept the v2 `meta` parameter without requiring any schema change
- Added a minimal read-only `PgJobQueue.snapshot()` so PG and FileSpool share the v2 protocol surface
- Added protocol-level unit coverage for PG queue v2 compatibility that does not require a live PostgreSQL instance
- Added a dedicated FileSpool regression test for old empty marker files falling back to legacy metadata
- Kept `peek()` semantics unchanged and did not introduce Task 5 metadata persistence, lease-history, or result storage

### TDD Log

#### RED

Command:

```powershell
python -m pytest tests/test_pg_queue.py -k "unit and (meta or snapshot)"
```

Key output:

```text
E   TypeError: PgJobQueue.enqueue() got an unexpected keyword argument 'meta'
E   AttributeError: 'PgJobQueue' object has no attribute 'snapshot'
```

Result: failed for the expected protocol-gap reasons before production changes.

#### GREEN

Commands:

```powershell
python -m pytest tests/test_reindex_queue.py tests/test_reindex_queue_cli.py
python -m pytest tests/test_pg_queue.py
```

Key output:

```text
29 passed in 0.27s
2 passed, 17 skipped in 0.12s
```

PG skip reason:

```text
无 memory.pg_dsn / CODEV_PLATFORM_MEMORY_DSN
```

Result: required queue/CLI regression suite passed; PG protocol unit tests passed; live PG integration cases skipped cleanly because this machine does not expose a PostgreSQL DSN.

### Implementation Notes

- `PgJobQueue.enqueue(..., meta=...)` now matches the `JobQueue` v2 signature and intentionally ignores metadata in Task 3 when the backing table has no metadata column.
- `PgJobQueue.snapshot()` uses the existing queue table as a read-only source:
  - `pending`: rows with `status='pending'` plus expired `running` rows
  - `active`: `running` rows whose lease has not expired
  - `results`: empty in Task 3
- Snapshot jobs continue to use legacy-default `JobMeta()` on PG because metadata persistence is explicitly deferred to Task 5.
- `peek()` remains the old pending-only/expired-only view, so status and worker behavior stay backward compatible.

### Files Changed

- `codev_platform/reindex/pg_queue.py`
- `tests/test_pg_queue.py`
- `tests/test_reindex_queue.py`
- `docs/plans/roadmap-2026-07-09/reindex-worker-v2-task-3-report-2026-07-14.md`

### Residual Concerns

- PG metadata is still protocol-compatible rather than feature-parity complete; `meta` is accepted but not persisted until Task 5.
- `snapshot().results` remains empty by design in this phase; any richer result/lease history semantics still belong to Task 5.
