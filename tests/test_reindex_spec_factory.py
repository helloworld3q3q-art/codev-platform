"""reindex attempt spec 工厂与输入选择边界测试。"""
from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

from codev_platform.core.runtime_models import RuntimeIdentity
from codev_platform.reindex.attempts import CanonicalJsonObject
from codev_platform.reindex.queue_ports import ClaimedJob, Job, JobMeta
from codev_platform.reindex.spec_factory import (
    AttemptInputSelection,
    AttemptSpecFactory,
    ConfiguredInputSelector,
)

_OID = "a" * 40
_RUNTIME = "b" * 64


def _identity() -> RuntimeIdentity:
    return RuntimeIdentity(
        mode="editable",
        runtime_revision=_RUNTIME,
        release_id=None,
        wheel_sha256=None,
        base_id=None,
        base_requirements_sha256=None,
        interpreter_realpath="C:/Python/python.exe",
        environment_prefix="C:/Python",
        source_root="D:/source/codev-platform",
    )


def _claim(*, target_commit: str | None = _OID) -> ClaimedJob:
    return ClaimedJob(
        job=Job(
            project_id="demo",
            kind="chroma",
            enqueued_at=1.0,
            meta=JobMeta(source="webhook", pull_policy="ff_only", target_commit=target_commit),
        ),
        claim_token="claim-secret",
        owner_token="owner-1",
        lease_expires_at=30.0,
    )


def _factory(selector, *, timeout: float = 30.0) -> AttemptSpecFactory:
    return AttemptSpecFactory(
        runtime_identity=_identity(),
        input_selector=selector,
        timeout_for_kind=lambda _kind: timeout,
        attempt_id_factory=lambda: "attempt-1",
        fence_factory=lambda: "fence-1",
    )


def _attempt_spec_calls(path: Path) -> list[tuple[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    calls: list[tuple[str, str]] = []

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.functions: list[str] = []
            self.constructor_names = {"AttemptSpec"}

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            for alias in node.names:
                if alias.name == "AttemptSpec":
                    self.constructor_names.add(alias.asname or alias.name)
            self.generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.functions.append(node.name)
            self.generic_visit(node)
            self.functions.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node: ast.Call) -> None:
            name = node.func.id if isinstance(node.func, ast.Name) else ""
            is_constructor = name in self.constructor_names or (
                isinstance(node.func, ast.Attribute) and node.func.attr == "AttemptSpec"
            )
            if is_constructor:
                calls.append((path.as_posix(), self.functions[-1] if self.functions else "<module>"))
            self.generic_visit(node)

    _Visitor().visit(tree)
    return calls


def test_attempt_spec仅由业务工厂直接构造且持久化恢复走受控解码() -> None:
    root = Path(__file__).resolve().parents[1]
    calls: list[tuple[str, str]] = []
    for path in (root / "codev_platform").rglob("*.py"):
        calls.extend(_attempt_spec_calls(path))

    normalized = {(Path(path).name, function) for path, function in calls}
    assert normalized == {("spec_factory.py", "create")}


def test_factory_uses_one_selector_and_cached_runtime_identity() -> None:
    calls: list[ClaimedJob] = []

    class _Selector:
        def select(self, claim: ClaimedJob) -> AttemptInputSelection:
            calls.append(claim)
            return AttemptInputSelection(
                input_kind="configured",
                input_payload=CanonicalJsonObject.from_value({"project_id": claim.job.project_id}),
            )

    claim = _claim()
    spec = _factory(_Selector()).create(claim)

    assert calls == [claim]
    assert spec.attempt_id == "attempt-1"
    assert spec.fence == "fence-1"
    assert spec.project_id == "demo"
    assert spec.kind == "chroma"
    assert spec.target_commit == _OID
    assert spec.timeout_sec == 30.0
    assert spec.runtime_revision == _RUNTIME
    assert "claim-secret" not in spec.input_payload.text


@pytest.mark.parametrize("timeout", [0.0, -1.0, math.inf, math.nan])
def test_factory_rejects_missing_target_commit_or_nonpositive_timeout(timeout: float) -> None:
    selector = ConfiguredInputSelector()
    with pytest.raises(ValueError):
        _factory(selector).create(_claim(target_commit=None))
    with pytest.raises(ValueError):
        _factory(selector, timeout=timeout).create(_claim())


def test_configured_selector_emits_only_allowlisted_payload_without_git_or_io() -> None:
    selected = ConfiguredInputSelector().select(_claim())

    assert selected.input_kind == "configured"
    assert selected.input_payload.to_value() == {"project_id": "demo"}
    source = (Path(__file__).resolve().parents[1] / "codev_platform/reindex/spec_factory.py").read_text(
        encoding="utf-8",
    )
    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    assert not imported & {"subprocess", "pathlib", "codev_platform.reindex.git_sync"}


def test_isolated_attempt_uses_kind_specific_cpu_timeouts(monkeypatch) -> None:
    from codev_platform.reindex.isolated_worker_composer import _attempt_timeout

    monkeypatch.setenv("PLATFORM_EMBED_DEVICE", "cpu")

    # 外层必须给内部 runner 留出返回 rc、写 journal 与回收进程的窗口，不能同秒抢杀。
    assert _attempt_timeout({}, "code_vec") == 11100.0
    assert _attempt_timeout({}, "chroma") == 7500.0
