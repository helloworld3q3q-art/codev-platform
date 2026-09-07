"""隔离 executor、configured 输入策略与结果证明测试。"""

from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path

import pytest

from codev_platform.core.runtime_models import RuntimeIdentity
from codev_platform.reindex import executor
from codev_platform.reindex.attempt_inputs import (
    MaterializedInput,
)
from codev_platform.reindex.attempts import (
    AttemptSpec,
    CanonicalJsonObject,
)

_OID = "a" * 40
_EXTRA_OID = "b" * 40
_TREE = "c" * 40
_EXTRA_TREE = "d" * 40
_RUNTIME = "e" * 64


def _spec(**changes: object) -> AttemptSpec:
    value = AttemptSpec(
        schema_version=1,
        attempt_id="attempt-1",
        fence="fence-1",
        project_id="demo",
        kind="chroma",
        input_kind="configured",
        input_payload=CanonicalJsonObject.from_value({"project_id": "demo"}),
        target_commit=_OID,
        timeout_sec=30.0,
        runtime_revision=_RUNTIME,
    )
    return dataclasses.replace(value, **changes) if changes else value


def _materialized(root: Path) -> MaterializedInput:
    return MaterializedInput(
        root=str(root),
        input_commits=(("main", _OID),),
        input_trees=(("main", _TREE),),
    )


def _success_proof() -> CanonicalJsonObject:
    return CanonicalJsonObject.from_value({"success": True, "kind": "configured"})


def _identity(revision: str = _RUNTIME) -> RuntimeIdentity:
    return RuntimeIdentity(
        mode="installed",
        runtime_revision=revision,
        release_id=None,
        wheel_sha256=None,
        base_id=None,
        base_requirements_sha256=None,
        interpreter_realpath=str(Path(sys.executable).resolve()),
        environment_prefix=str(Path(sys.prefix).resolve()),
        source_root=None,
    )


class _Strategy:
    def __init__(
        self,
        root: Path,
        *,
        events: list[str] | None = None,
        proof: CanonicalJsonObject | None = None,
        error: BaseException | None = None,
        verify_error: BaseException | None = None,
    ) -> None:
        self.root = root
        self.events = events if events is not None else []
        self.proof = proof if proof is not None else _success_proof()
        self.error = error
        self.verify_error = verify_error
        self.pids: list[int] = []

    def materialize(self, _spec: AttemptSpec) -> MaterializedInput:
        self.events.append("materialize")
        self.pids.append(os.getpid())
        if self.error is not None:
            raise self.error
        return _materialized(self.root)

    def verify(self, _spec: AttemptSpec, _value: MaterializedInput) -> CanonicalJsonObject:
        self.events.append("verify")
        self.pids.append(os.getpid())
        if self.verify_error is not None:
            raise self.verify_error
        return self.proof


class _Runner:
    kind = "chroma"
    last_note = ""
    last_log_ref = "logs/demo__chroma.log"

    def __init__(self, rc: int, events: list[str] | None = None) -> None:
        self.rc = rc
        self.events = events if events is not None else []
        self.calls: list[tuple[str, Path, dict, int]] = []

    def run(self, project_id: str, root: Path, cfg: dict) -> int:
        self.events.append("runner")
        self.calls.append((project_id, root, cfg, os.getpid()))
        return self.rc


def _install_executor(
    monkeypatch: pytest.MonkeyPatch,
    runner: _Runner,
    *,
    revision: str = _RUNTIME,
) -> None:
    monkeypatch.setattr(
        executor.runtime_identity_module, "runtime_identity", lambda: _identity(revision)
    )
    monkeypatch.setattr(executor._runners, "get_runner", lambda _kind: runner)
