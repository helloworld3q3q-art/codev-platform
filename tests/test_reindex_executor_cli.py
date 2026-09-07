"""隔离 executor、configured 输入策略与结果证明测试。"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from codev_platform.reindex import executor
from codev_platform.reindex.attempt_inputs import (
    freeze_attempt_input_strategies,
)
from codev_platform.reindex.attempts import (
    AttemptOutcome,
    read_attempt_result,
    write_attempt_spec_atomic,
)

from tests import reindex_executor_support as support


def test_executor_cli_reads_spec_and_atomically_writes_matching_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec_path = tmp_path / "spec.json"
    result_path = tmp_path / "result.json"
    write_attempt_spec_atomic(spec_path, support._spec())
    strategy = support._Strategy(tmp_path)
    runner = support._Runner(0)
    cfg = {"same": object()}
    support._install_executor(monkeypatch, runner)
    monkeypatch.setattr(executor, "load_config", lambda: cfg)
    monkeypatch.setattr(
        executor,
        "_bootstrap_strategies",
        lambda actual_cfg: (
            freeze_attempt_input_strategies({"configured": strategy})
            if actual_cfg is cfg
            else pytest.fail("CLI 重复加载或替换了 cfg")
        ),
    )

    rc = executor.main(["--spec", str(spec_path), "--result", str(result_path)])

    assert rc == 0
    result = read_attempt_result(result_path)
    assert result.attempt_id == "attempt-1"
    assert result.outcome is AttemptOutcome.SUCCEEDED
    assert runner.calls[0][2] is cfg
    assert not list(tmp_path.glob("*.tmp"))


def test_executor_has_no_queue_supervisor_manifest_or_health_import() -> None:
    path = Path(executor.__file__).resolve()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    forbidden = ("queue", "supervisor", "manifest", "health", "workspace")
    assert not [module for module in modules if any(name in module for name in forbidden)]


def test_executor_fresh_process_has_no_indirect_queue_dependency() -> None:
    script = (
        "import json, sys; "
        "import codev_platform.reindex.executor; "
        "print(json.dumps({"
        "'queue': 'codev_platform.reindex.queue' in sys.modules, "
        "'queue_ports': 'codev_platform.reindex.queue_ports' in sys.modules, "
        "'worker': 'codev_platform.reindex.worker' in sys.modules}))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        timeout=10,
    )

    assert json.loads(completed.stdout) == {
        "queue": False,
        "queue_ports": False,
        "worker": False,
    }


def test_concrete_strategy_is_instantiated_only_in_child_bootstrap() -> None:
    root = Path(__file__).resolve().parents[1] / "codev_platform"
    call_files: dict[str, set[str]] = {
        "ConfiguredAttemptInputStrategy": set(),
        "freeze_attempt_input_strategies": set(),
    }
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id in call_files:
                call_files[node.func.id].add(path.name)

    assert call_files == {
        "ConfiguredAttemptInputStrategy": {"executor_bootstrap.py"},
        "freeze_attempt_input_strategies": {"executor_bootstrap.py"},
    }
