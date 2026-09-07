"""memory import (P2 迁移) 单测 —— dry-run 解析 + slug 归一 + CLI 接线。真写入 PG 由冒烟另验。"""
from __future__ import annotations


def _write_md(d, name, text):
    (d / name).write_text(text, encoding="utf-8")


def test_iter_entries_classifies_kind_and_redline(tmp_path):
    from codev_platform.agent.memory_import import iter_entries
    _write_md(tmp_path, "feedback_dark_mode.md",
              "---\nname: dark mode\ndescription: 偏好深色\n---\n正文")
    _write_md(tmp_path, "reference_dashboard.md",
              "---\nname: 面板\ndescription: 看板链接\n---\nbody")
    _write_md(tmp_path, "feedback_no_autonomous_push.md",
              "---\nname: 禁自动推送\n---\nbody")
    rows = {r["stem"]: r for r in iter_entries(tmp_path)}
    assert rows["feedback_dark_mode.md".replace(".md", "")]  # stem 无扩展名
    assert rows["feedback_dark_mode"]["kind"] == "preference"
    assert rows["reference_dashboard"]["kind"] == "reference"
    # 内置禁止集合 → redline
    assert rows["feedback_no_autonomous_push"]["is_redline"] is True
    assert rows["feedback_dark_mode"]["is_redline"] is False


def test_run_import_dry_run_no_pg(tmp_path):
    from codev_platform.agent.memory_import import run_import
    _write_md(tmp_path, "feedback_x.md", "---\nname: x\ndescription: d\n---\nb")
    out = []
    rc = run_import(tmp_path, scope="personal", scope_ref="alice", apply=False, echo=out.append)
    assert rc == 0
    joined = "\n".join(out)
    assert "DRY-RUN" in joined and "feedback_x" in joined


def test_run_import_missing_dir(tmp_path):
    from codev_platform.agent.memory_import import run_import
    rc = run_import(tmp_path / "nope", apply=False, echo=lambda _m: None)
    assert rc == 2


def test_cli_registers_memory_import_md():
    from codev_platform.cli import build_parser
    parser = build_parser()
    ns = parser.parse_args(["memory", "import-md", "/some/path", "--scope", "project",
                            "--scope-ref", "proj1", "--apply"])
    assert ns.action == "import-md" and ns.path == "/some/path"
    assert ns.scope == "project" and ns.scope_ref == "proj1" and ns.apply is True
    assert callable(ns.func)


def test_cli_memory_initdb_doctor_still_parse():
    # 既有子命令不被 import-md 接入破坏(从 positional choice 改 subparsers, 行为兼容)
    from codev_platform.cli import build_parser
    parser = build_parser()
    for act in ("init-db", "doctor"):
        ns = parser.parse_args(["memory", act])
        assert ns.action == act and callable(ns.func)
