"""版本化运行时操作规程的安全顺序与真实冒烟契约测试。"""

from __future__ import annotations

from pathlib import Path

_RUNBOOK = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "plans"
    / "roadmap-2026-07-11"
    / "reindex-runtime-release-runbook-2026-07-11.md"
)


def _runbook() -> str:
    return _RUNBOOK.read_text(encoding="utf-8")


def _ordered(text: str, *markers: str) -> None:
    positions = [text.index(marker) for marker in markers]
    assert positions == sorted(positions), f"操作规程顺序漂移：{markers}"


def _section(text: str, start: str, end: str) -> str:
    return text[text.index(start) : text.index(end)]


def test_runbook_defaults_to_readonly_and_requires_explicit_apply_guard() -> None:
    text = _runbook()

    assert "默认只执行只读预检" in text
    assert 'test "${CODEV_RUNTIME_APPLY:-否}" = "是"' in text
    _ordered(
        text,
        "## 3. 只读预检",
        "## 4. 显式变更授权",
        'test "${CODEV_RUNTIME_APPLY:-否}" = "是"',
        'runtime_root activate "$BASELINE_RELEASE_ID"',
        "systemctl stop codev-webhook.service",
    )


def test_runbook_separates_user_builds_from_root_runtime_mutations() -> None:
    text = _runbook()
    user_wrapper = _section(text, "runtime_user() {", "runtime_root() {")
    root_wrapper = _section(text, "runtime_root() {", "assert_dead() {")

    assert "RUNTIME_ROOT=/var/lib/codev-platform/runtime" in text
    assert "RUNTIME_ROOT=/opt/codev-platform" not in text
    assert "sudo" not in user_wrapper
    assert "sudo -n env" in root_wrapper
    assert "\nruntime() {" not in text

    assert text.count("runtime_user build") == 2
    assert "runtime_user status --json" in text
    assert "runtime_user verify" in text
    assert "runtime_root build" not in text
    assert "runtime_root status" not in text
    assert "runtime_root verify" not in text

    for operation in ("base", "stage", "activate", "rollback"):
        assert f"runtime_root {operation}" in text


def test_runbook_first_cutover_builds_real_main_base_and_proves_two_releases() -> None:
    text = _runbook()

    assert "rev-parse --git-path plan-c-main-base" in text
    assert "worktree add --detach" in text
    assert "worktree remove --force" in text
    assert 'runtime_user verify "$BASELINE_RELEASE_ID"' in text
    assert 'runtime_user verify "$NEW_RELEASE_ID"' in text
    assert 'runtime_user verify "$PREVIOUS_RELEASE_ID"' in text
    assert 'test -n "$PREVIOUS_RELEASE_ID"' in text
    _ordered(
        text,
        "## 5. 构建并验证基线版本",
        "## 6. 构建并验证新版本",
        "## 7. 形成可回滚双版本状态",
        "## 8. 预装与目标用户探针",
        "## 9. 维护窗口切换",
        "## 10. 受控 reindex、真实冒烟与恢复入口",
    )
    assert text.index('runtime_user verify "$PREVIOUS_RELEASE_ID"') < text.index(
        "systemctl stop codev-webhook.service"
    )


def test_runbook_rollback_verifies_previous_before_pointer_change_and_restart() -> None:
    text = _runbook()
    rollback = _section(text, "## 11. 回滚", "## 12. 收尾与证据")

    _ordered(
        rollback,
        "systemctl stop codev-webhook.service",
        "assert_dead",
        'runtime_user verify "$PREVIOUS_RELEASE_ID"',
        "runtime_root rollback",
        "systemctl restart",
        "runtime_identity().as_dict()",
        "受控 reindex",
        "真实 MCP 冒烟",
    )
    assert "禁止 schema downgrade" in rollback
    assert "不得删除失败版本" in rollback


def test_runbook_rollback_probes_identity_with_the_rolled_back_interpreter() -> None:
    text = _runbook()
    rollback = _section(text, "## 11. 回滚", "## 12. 收尾与证据")
    after_pointer_change = rollback[rollback.index("runtime_root rollback") :]

    assert '"$RUNTIME_ROOT/current/venv/bin/python" -I -c' in after_pointer_change
    assert (
        "from codev_platform.core.runtime_identity import runtime_identity"
        in after_pointer_change
    )
    assert "runtime_identity().as_dict()" in after_pointer_change
    assert "runtime status --json" not in after_pointer_change
    assert ".release_id" in after_pointer_change
    assert '"$PREVIOUS_RELEASE_ID"' in after_pointer_change


def test_runbook_process_tree_proof_checks_pid_and_cgroup() -> None:
    text = _runbook()

    assert "systemctl show --property=MainPID --value" in text
    assert "systemctl show --property=ControlGroup --value" in text
    assert "cgroup.procs" in text


def test_runbook_real_mcp_smoke_requires_structured_tool_success() -> None:
    text = _runbook()
    smoke = _section(text, "### 10.3 真实 MCP 冒烟", "### 10.4 恢复 webhook")

    for tool in ("list_collections", "codegraph_status", "search_nodes", "recall"):
        assert f'"{tool}"' in smoke
    for contract in (
        "result.isError",
        "json.loads",
        'payload.get("error")',
        "isinstance(payload, dict)",
    ):
        assert contract in smoke
    assert "仅 HTTP 200 不算成功" in smoke


def test_runbook_hands_reindex_to_authoritative_maintenance_protocol() -> None:
    text = _runbook()
    reindex = _section(text, "### 10.2 受控 reindex", "### 10.3 真实 MCP 冒烟")

    assert "reindex-isolated-maintenance-wsl-runbook-2026-07-13.md" in reindex
    assert "active attempt 清零" in reindex
    for scope in ("chroma", "codegraph", "ingest", "code_vec"):
        assert scope in reindex
    assert "目标提交完整 SHA" in reindex
    assert "不得把队列为空当作成功" in reindex


def test_runbook_keeps_plan_c_scope_and_evidence_gates() -> None:
    text = _runbook()

    assert "diff --name-only" in text
    assert "^codev_platform/reindex/" in text
    assert "diff --check" in text
    assert "失败版本与 .incomplete 证据" in text
    assert "禁止生成正式 requirements lock" in text
    assert "禁止手工删除 current、previous" in text
