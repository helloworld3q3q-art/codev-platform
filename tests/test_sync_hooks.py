"""sync-hooks 的 settings merge 逻辑: 幂等 + 保留业务仓现有 settings(不覆盖)。

cmd_sync_hooks 把 MCP-first PreToolUse(Grep) hook 注入业务仓 .claude/settings.json,
关键不变量: 重复 sync 不叠加, 已有 settings/其他 hook 不被破坏。
"""
from __future__ import annotations

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
