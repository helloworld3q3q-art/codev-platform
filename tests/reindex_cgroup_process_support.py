from __future__ import annotations

import time
from pathlib import Path


from codev_platform.reindex.attempt_process import (
    Deadline,
    ExecutionHandle,
)
from codev_platform.reindex.attempts import AttemptJournalEntry
from codev_platform.reindex.cgroup_process import (
    CGROUP_CONTAINMENT_KIND,
    CgroupAttemptProcessBackend,
    encode_cgroup_native_ref,
)
from codev_platform.reindex.posix_process_identity import (
    LinuxProcessInfo,
    LinuxProcessTable,
)

_BOOT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_OLD_BOOT_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
_FIXTURE = Path(__file__).parent / "fixtures" / "reindex_process_fixture.py"


def _wait_for_pid(path: Path, timeout: float = 3.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = path.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            value = ""
        if value.isdigit() and int(value) > 0:
            return int(value)
        time.sleep(0.01)
    raise AssertionError("进程夹具 PID 未按时写完整")

class _FakeClock:
    def __init__(self) -> None:
        self.monotonic_value = 100.0
        self.wall_value = 1_800_000_000.0

    def monotonic(self) -> float:
        return self.monotonic_value

    def wall(self) -> float:
        return self.wall_value

    def sleep(self, seconds: float) -> None:
        self.monotonic_value += seconds
        self.wall_value += seconds


class _FakeCgroupFs:
    def __init__(self, root: Path, *, populated: bool = True) -> None:
        self.root = root
        self.boot_id = _BOOT_ID
        self._populated = populated
        self.present = True
        self.kills = 0
        self.removed = 0
        self.readiness_deadlines: list[Deadline] = []
        self.readiness_failure: BaseException | None = None

    def assert_ready(self, deadline: Deadline) -> None:
        self.readiness_deadlines.append(deadline)
        if self.readiness_failure is not None:
            raise self.readiness_failure

    def create_attempt(self, attempt_id: str) -> str:
        native_ref = encode_cgroup_native_ref(self.boot_id, attempt_id)
        self.path_for(native_ref).mkdir(parents=True, exist_ok=False)
        return native_ref

    def path_for(self, native_ref: str) -> Path:
        return self.root / native_ref.rsplit(":", 1)[-1]

    def contains_pid(self, native_ref: str, pid: int) -> bool:
        return self.present and pid > 0

    def populated(self, native_ref: str) -> bool:
        if not self.present:
            raise FileNotFoundError(native_ref)
        return self._populated

    def kill(self, native_ref: str) -> None:
        self.kills += 1
        self._populated = False

    def exists(self, native_ref: str) -> bool:
        return self.present

    def exists_attempt(self, attempt_id: str) -> bool:
        return self.present

    def remove_empty(self, native_ref: str) -> None:
        self.removed += 1
        self.present = False


class _StubbornCgroupFs(_FakeCgroupFs):
    def kill(self, native_ref: str) -> None:
        self.kills += 1


class _FakeProcessTable:
    def __init__(self) -> None:
        self.boot_id = _BOOT_ID
        self.info = LinuxProcessInfo(321, 1, 321, 321, "S", 456)
        self.signals: list[int] = []

    def read(self, pid: int):
        return self.info if pid == self.info.pid else None

    def build_identity(self, info: LinuxProcessInfo, native_ref: str) -> str:
        return LinuxProcessTable.build_identity_from_values(
            pid=info.pid,
            native_ref=native_ref,
            boot_id=self.boot_id,
            start_ticks=info.start_ticks,
        )

    def signal_same(self, info: LinuxProcessInfo, sig: int) -> bool:
        self.signals.append(sig)
        return True


def _handle(fs: _FakeCgroupFs, table: _FakeProcessTable) -> ExecutionHandle:
    native_ref = encode_cgroup_native_ref(fs.boot_id, "attempt-one")
    return ExecutionHandle(
        attempt_id="attempt-one",
        pid=table.info.pid,
        process_identity=table.build_identity(table.info, native_ref),
        containment_kind=CGROUP_CONTAINMENT_KIND,
        native_ref=native_ref,
        started_at=1_700_000_000.0,
    )


def _journal(handle: ExecutionHandle) -> AttemptJournalEntry:
    return AttemptJournalEntry(
        schema_version=1,
        owner_token="owner",
        claim_token="claim",
        attempt_id=handle.attempt_id,
        fence="fence",
        project_id="project",
        kind="chroma",
        spec_path="/tmp/spec.json",
        result_path="/tmp/result.json",
        pid=handle.pid,
        process_identity=handle.process_identity,
        containment_kind=handle.containment_kind,
        native_ref=handle.native_ref,
        state="running",
        started_at=handle.started_at,
        timeout_sec=10.0,
    )


def _claimed_journal(attempt_id: str = "attempt-crash-window") -> AttemptJournalEntry:
    return AttemptJournalEntry(
        schema_version=1,
        owner_token="owner",
        claim_token="claim",
        attempt_id=attempt_id,
        fence="fence",
        project_id="project",
        kind="chroma",
        spec_path="/tmp/spec.json",
        result_path="/tmp/result.json",
        pid=None,
        process_identity=None,
        containment_kind=None,
        native_ref=None,
        state="claimed",
        started_at=1_700_000_000.0,
        timeout_sec=10.0,
    )


def _backend(fs, table, clock: _FakeClock) -> CgroupAttemptProcessBackend:
    return CgroupAttemptProcessBackend(
        filesystem=fs,
        process_table=table,
        poll_interval=0.01,
        monotonic=clock.monotonic,
        wall_clock=clock.wall,
        sleeper=clock.sleep,
    )
