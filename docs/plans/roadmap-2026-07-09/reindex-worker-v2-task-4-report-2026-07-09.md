# Task 4 Report: FileSpool Queue V2

## Scope

- Reworked `FileSpoolQueue` onto `pending/`, `active/`, and `results/` JSON sidecars
- Added atomic same-directory temp-write + `replace()` persistence for every sidecar write
- Implemented pending claim to active lease with claim-token protection
- Preserved dirty `pending` alongside `active` so re-enqueue during execution no longer overwrites the active lease record
- Added FileSpool `release()`, `renew()`, `reclaim_stale_own()`, and `break_lease()` lifecycle operations
- Kept legacy root markers lazily readable and migrated them into `pending/*.json` on read/claim
- Preserved unknown legacy kind clearability through the new sidecar flow
- Updated queue/CLI/worker tests to lock the new semantics

## TDD Log

### RED

Command:

```powershell
python -m pytest tests/test_reindex_queue.py tests/test_reindex_queue_cli.py tests/test_reindex_worker_affinity.py
```

Key failures:

```text
AssertionError: pending/...json sidecar does not exist
TypeError: FileSpoolQueue.__init__() got an unexpected keyword argument 'lease_ttl_sec'
AssertionError: legacy root marker still exists after peek()
```

Result: failed for the expected FileSpool V2 gaps before production changes.

### GREEN

Command:

```powershell
python -m pytest tests/test_reindex_queue.py tests/test_reindex_queue_cli.py tests/test_reindex_worker_affinity.py
```

Key output:

```text
collected 45 items
45 passed in 0.76s
```

Result: FileSpool queue v2 behavior and its CLI/worker integration regressions passed locally.

## Implementation Notes

- `enqueue()` now always writes `pending/<key>.json`; when `active/<key>.json` exists it preserves active and refreshes only pending.
- `pending()` now claims pending sidecars into `active/` and returns `Job.token`; `peek()` remains pending-only.
- `complete(job)` removes only the matching-token active record; if dirty pending exists it returns `False` and leaves pending intact.
- `release(job)` and `break_lease(job)` both clear active without deleting pending; `release()` is token-guarded while `break_lease()` is management-only.
- `renew(job)` extends the lease only when the active claim token still matches.
- `reclaim_stale_own()` restores owned active records back to pending for local restart recovery.
- `snapshot()` now surfaces all three buckets from disk: pending, active, and results.

## Files Changed

- `codev_platform/reindex/queue.py`
- `tests/test_reindex_queue.py`
- `tests/test_reindex_queue_cli.py`
- `tests/test_reindex_worker_affinity.py`
- `docs/plans/roadmap-2026-07-09/reindex-worker-v2-task-4-report-2026-07-09.md`

## Residual Concerns

- FileSpool lease expiry is still management-driven rather than automatic takeover; recovery paths are `release()`, `reclaim_stale_own()`, and `break_lease()`, which matches this task’s scope and avoids touching PG / Task 5.

---

## Task 4 Review Fix (2026-07-09)

### Scope

- Fixed FileSpool v2 same-key concurrency windows only; PG queue and Task 5 stayed untouched
- Updated stale FileSpool lease comments in `codev_platform/reindex/worker.py`

### TDD Log

#### RED

Command:

```powershell
python -m pytest tests/test_reindex_queue.py -k "drop_reenqueue or overwrite_new_pending"
```

Observed failures:

```text
AssertionError: pending/...json sidecar missing after pending() claim + re-enqueue race
AssertionError: break_lease() restored stale active payload over fresh pending meta/enqueued_at
```

Result: failed for the expected claim/release race windows from review.

#### GREEN

Command:

```powershell
python -m pytest tests/test_reindex_queue.py tests/test_reindex_queue_cli.py tests/test_reindex_worker_affinity.py
```

Key output:

```text
collected 47 items
47 passed in 0.95s
```

Result: FileSpool v2 concurrency transitions, CLI management flows, and worker affinity tests passed locally.

### Fix Notes

- Added per-key filesystem locks at `locks/<key>.lock` using atomic `mkdir`/`rmdir`, with lock hold time limited to same-key pending/active/results/legacy transitions
- `pending()` now re-reads pending and active inside the key lock before claim, so active creation and pending unlink are no longer based on stale pre-lock state
- `enqueue()` now always mutates only `pending/<key>.json`, even when an active lease exists
- `complete()`, `release()`, `renew()`, `reclaim_stale_own()`, and `break_lease()` now re-check state inside the key lock and never overwrite an already-present fresh pending sidecar
- Legacy root-marker lazy migration now runs per key under the same lock, preserving migration and unknown-kind clearability without reintroducing write races
- Added regression tests that simulate claim-time re-enqueue and break-lease-time fresh pending writes against real spool files
