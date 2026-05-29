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
