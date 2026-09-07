"""sync-hooks 的 settings merge 逻辑: 幂等 + 保留业务仓现有 settings(不覆盖)。

cmd_sync_hooks 把 MCP-first PreToolUse(Grep) hook 注入业务仓 .claude/settings.json,
关键不变量: 重复 sync 不叠加, 已有 settings/其他 hook 不被破坏。
"""
from __future__ import annotations

from argparse import Namespace

from codev_platform.cli_cmds.sync import _merge_grep_hook


def test_merge_adds_grep_hook_to_empty():
    settings: dict = {}
    changed = _merge_grep_hook(settings)
    assert changed is True
    pre = settings["hooks"]["PreToolUse"]
    assert len(pre) == 1
    assert pre[0]["matcher"] == "Grep"
    assert any("mcp-first-guard" in str(a) for a in pre[0]["hooks"][0]["args"])


def test_merge_is_idempotent():
    settings: dict = {}
    _merge_grep_hook(settings)
    changed2 = _merge_grep_hook(settings)
    assert changed2 is False, "重复 merge 不应再改"
    assert len(settings["hooks"]["PreToolUse"]) == 1, "PreToolUse 不应叠加"


def test_merge_preserves_existing_settings():
    settings: dict = {
        "permissions": {"allow": ["Bash(ls)"], "defaultMode": "bypassPermissions"},
        "hooks": {"PostToolUse": [{"matcher": "Write", "hooks": []}]},
    }
    _merge_grep_hook(settings)
    assert settings["permissions"]["allow"] == ["Bash(ls)"]
    assert settings["permissions"]["defaultMode"] == "bypassPermissions"
    assert len(settings["hooks"]["PostToolUse"]) == 1
    assert settings["hooks"]["PreToolUse"][0]["matcher"] == "Grep"


def test_merge_coexists_with_other_pretooluse_hook():
    # 业务仓已有别的 PreToolUse hook(非 Grep) → 共存
    settings: dict = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": []}]}}
    changed = _merge_grep_hook(settings)
    assert changed is True
    matchers = [e["matcher"] for e in settings["hooks"]["PreToolUse"]]
    assert "Bash" in matchers and "Grep" in matchers


def test_sync_resources_to_repo(tmp_path):
    """sync_resources_to: rules/skills/hooks 同步进 <repo>/.claude/ + settings 装 MCP-first hook(repo-aware)。"""
    import json as _json
    from codev_platform.cli_cmds.sync import sync_resources_to
    repo = tmp_path / "biz"
    repo.mkdir()
    assert sync_resources_to(repo) is True
    claude = repo / ".claude"
    assert any((claude / "rules").glob("*.md"))                 # 真资源拷贝
    assert (claude / "hooks" / "mcp-first-guard.js").is_file()
    settings = _json.loads((claude / "settings.json").read_text(encoding="utf-8"))
    assert any(e.get("matcher") == "Grep" for e in settings["hooks"]["PreToolUse"])


def test_sync_skills_can_target_both_surfaces_and_only_selected_resource(
    tmp_path,
    monkeypatch,
):
    from codev_platform.cli_cmds import sync as sync_mod

    source = tmp_path / "source"
    (source / "keep").mkdir(parents=True)
    (source / "skip").mkdir(parents=True)
    (source / "keep" / "SKILL.md").write_text("keep", encoding="utf-8")
    (source / "skip" / "SKILL.md").write_text("skip", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    monkeypatch.setattr(sync_mod, "_SKILLS_SRC", source)
    monkeypatch.setattr(
        sync_mod,
        "_CODEX_COMPATIBLE_ROOTS",
        {"rules": frozenset(), "skills": frozenset({"keep"})},
    )

    rc = sync_mod.cmd_sync_skills(
        Namespace(dry_run=False, target="both", only=["keep"]),
    )

    assert rc == 0
    for surface in (".codex", ".claude"):
        assert (repo / surface / "skills" / "keep" / "SKILL.md").read_text(
            encoding="utf-8",
        ) == "keep"
        assert not (repo / surface / "skills" / "skip").exists()


def test_sync_only_missing_resource_fails_instead_of_silent_success(tmp_path, monkeypatch):
    from codev_platform.cli_cmds import sync as sync_mod

    source = tmp_path / "source"
    source.mkdir()
    (source / "present.md").write_text("present", encoding="utf-8")
    destination = tmp_path / "destination"

    assert sync_mod._sync_dir(
        source,
        destination,
        "rules",
        False,
        only=["missing.md"],
    ) == -1
    assert not destination.exists()


def test_codex_compatible_resources_do_not_contain_claude_only_instructions():
    from codev_platform.cli_cmds import sync as sync_mod

    health = (sync_mod._SKILLS_SRC / "ai-health" / "SKILL.md").read_text(encoding="utf-8")
    mcp_rule = (sync_mod._RULES_SRC / "ai-tools-mcp.md").read_text(encoding="utf-8")

    assert "AskUserQuestion" not in health
    assert "Codex" in mcp_rule and "Claude Code" in mcp_rule
    assert ".codex/config.toml" in mcp_rule
    assert "不得把 18xxx/19xxx 写死" in mcp_rule


def test_codex_sync_fails_closed_for_undeclared_surface_resource(tmp_path, monkeypatch):
    from codev_platform.cli_cmds import sync as sync_mod

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    rc = sync_mod.cmd_sync_rules(
        Namespace(dry_run=False, target="codex", only=["windows-powershell.md"]),
    )

    assert rc == 1
    assert not (repo / ".codex" / "rules" / "windows-powershell.md").exists()
