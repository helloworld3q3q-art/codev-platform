from __future__ import annotations

import inspect

import pytest

from codev_platform.reindex.attempt_process import (
    AttemptProcessBackend,
    ProcessBackendReadinessError,
    execution_handle_from_journal,
)


from tests import reindex_attempt_process_support as support


def test_backend_protocol_exposes_prepare_activate_and_shared_deadline() -> None:
    assert_ready = inspect.signature(AttemptProcessBackend.assert_ready)
    prepare = inspect.signature(AttemptProcessBackend.prepare)
    activate = inspect.signature(AttemptProcessBackend.activate)
    terminate = inspect.signature(AttemptProcessBackend.terminate)
    recover = inspect.signature(AttemptProcessBackend.recover)
    recover_handle = inspect.signature(AttemptProcessBackend.recover_handle)
    confirm_dead = inspect.signature(AttemptProcessBackend.confirm_dead)
    confirm_reference_dead = inspect.signature(AttemptProcessBackend.confirm_reference_dead)

    assert tuple(assert_ready.parameters) == ("self", "deadline")
    assert assert_ready.parameters["deadline"].annotation == "Deadline"
    assert assert_ready.return_annotation == "None"
    assert tuple(prepare.parameters) == (
        "self",
        "attempt_id",
        "argv",
        "cwd",
        "bootstrap_log",
        "deadline",
    )
    assert tuple(activate.parameters) == ("self", "handle", "deadline")
    assert tuple(terminate.parameters) == ("self", "handle", "grace_sec", "deadline")
    assert tuple(recover.parameters) == ("self", "journal", "deadline")
    assert tuple(recover_handle.parameters) == ("self", "handle", "deadline")
    assert tuple(confirm_dead.parameters) == ("self", "handle", "deadline")
    assert tuple(confirm_reference_dead.parameters) == ("self", "reference", "deadline")
    assert prepare.return_annotation == "ExecutionHandle"
    assert activate.return_annotation == "None"
    assert terminate.parameters["deadline"].annotation == "Deadline"
    assert terminate.return_annotation == "TerminationReport"
    assert recover.return_annotation == "RecoveryReport"
    assert recover_handle.return_annotation == "RecoveryReport"

    source = inspect.getsource(AttemptProcessBackend)
    assert "os.name" not in source
    assert "sys.platform" not in source
    assert "kill_timeout_sec" not in source
    assert "start" not in AttemptProcessBackend.__dict__


def test_readiness_error_is_a_fail_closed_runtime_error() -> None:
    error = ProcessBackendReadinessError("生产 containment 不可用")

    assert isinstance(error, RuntimeError)
    assert str(error) == "生产 containment 不可用"


def test_backend_protocol_documents_blocked_prepare_and_activation_failure() -> None:
    prepare_doc = inspect.getdoc(AttemptProcessBackend.prepare)
    activate_doc = inspect.getdoc(AttemptProcessBackend.activate)

    assert prepare_doc is not None and "目标尚未执行" in prepare_doc
    assert "持久化" in prepare_doc and "activate" in prepare_doc
    assert activate_doc is not None and "AttemptProcessStartError" in activate_doc
    assert "持久化" in activate_doc


def test_backend_prepare_paths_remain_typed_at_protocol_boundary() -> None:
    signature = inspect.signature(AttemptProcessBackend.prepare)

    assert signature.parameters["cwd"].annotation == "Path"
    assert signature.parameters["bootstrap_log"].annotation == "Path"


def test_execution_handle_from_journal_accepts_all_or_no_process_fields() -> None:
    restored = execution_handle_from_journal(support._journal())

    assert restored == support._handle()
    assert (
        execution_handle_from_journal(
            support._journal(
                pid=None,
                process_identity=None,
                containment_kind=None,
                native_ref=None,
                state="claimed",
            )
        )
        is None
    )


@pytest.mark.parametrize(
    "missing",
    ["pid", "process_identity", "containment_kind", "native_ref"],
)
def test_execution_handle_from_journal_rejects_partial_process_identity(missing: str) -> None:
    with pytest.raises(ValueError, match="journal|进程字段"):
        execution_handle_from_journal(support._journal(**{missing: None}))
