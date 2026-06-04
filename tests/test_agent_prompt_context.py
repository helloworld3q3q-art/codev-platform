"""system prompt 上下文注入测试 —— 让 agent "知道"当前 org/user/project."""
from __future__ import annotations

from codev_platform.agent.prompts import build_code_understanding_system, CODE_UNDERSTANDING_SYSTEM


def test_no_context_returns_base():
    assert build_code_understanding_system() == CODE_UNDERSTANDING_SYSTEM


def test_project_injected():
    s = build_code_understanding_system(project_id="openclaw-stock")
    assert "openclaw-stock" in s
    assert "当前会话上下文" in s
    assert CODE_UNDERSTANDING_SYSTEM in s  # 基础 prompt 仍在


def test_user_and_org_injected():
    s = build_code_understanding_system(project_id="p1", user_id="alice", org_id="acme")
    assert "alice" in s and "acme" in s and "p1" in s


def test_unspecified_project_noted():
    s = build_code_understanding_system(user_id="alice")
    assert "未指定" in s  # project 缺省时明确标注


def test_scope_guard_injected_with_project():
    # 防"绑错项目空转+捏造": 有 project 时注入 索引范围 + 越界 fail-fast + 不捏造 + 换 project_id 提示
    s = build_code_understanding_system(project_id="openclaw-stock")
    assert "索引范围" in s
    assert "没找到" in s and "换 project_id" in s
    assert "凭空捏造" in s


def test_scope_guard_absent_without_project():
    # 无 project(未指定)不注入越界提示(避免无意义文案)
    s = build_code_understanding_system(user_id="alice")
    assert "凭空捏造" not in s and "索引范围" not in s


def test_display_name_helper_graceful_on_unknown():
    from codev_platform.agent.prompts import _project_display_name
    # 未知项目 → None(不抛); 不影响 prompt 构建
    assert _project_display_name("no-such-project-xyz") is None
    s = build_code_understanding_system(project_id="no-such-project-xyz")
    assert "no-such-project-xyz" in s and "索引范围" in s  # 无 display_name 也照常注入守护
