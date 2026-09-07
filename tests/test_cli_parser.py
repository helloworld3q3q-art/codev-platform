"""Regression tests for codev_platform.cli.build_parser subcommand registration.

Bug context: the cross-platform ops subcommands (health / reindex / post-commit /
dirty-check / install-hooks / wait-for-reindex) replaced the old .ps1 scripts and
are wired in via ops.register_all. Guard against a submodule failing to register
(register_all swallows import errors), which would silently drop a subcommand.
"""
from __future__ import annotations

import codev_platform.cli as cli


def _subcommand_choices():
    parser = cli.build_parser()
    # the subparsers action carries the registered subcommand names as choices
    for action in parser._actions:
        if getattr(action, "choices", None) and "init" in action.choices:
            return set(action.choices)
    raise AssertionError("no subparsers action found")


def test_ops_subcommands_registered():
    choices = _subcommand_choices()
    for name in ("health", "reindex", "post-commit", "dirty-check",
                 "install-hooks", "wait-for-reindex", "reindex-queue", "runtime"):
        assert name in choices, f"{name} not registered"


def test_core_subcommands_registered():
    choices = _subcommand_choices()
    for name in ("init", "current", "validate", "config", "sync-rules", "sync-skills", "sync-hooks"):
        assert name in choices


def test_parser_parses_dirty_check():
    parser = cli.build_parser()
    args = parser.parse_args(["dirty-check", "--json"])
    assert args.cmd == "dirty-check"
    assert args.json is True


def test_parser_parses_wait_for_reindex_defaults():
    parser = cli.build_parser()
    args = parser.parse_args(["wait-for-reindex"])
    assert args.cmd == "wait-for-reindex"
    assert args.timeout_sec == 120


def test_parser_parses_narrow_codex_resource_sync():
    parser = cli.build_parser()
    args = parser.parse_args([
        "sync-skills",
        "--target",
        "both",
        "--only",
        "ai-health",
        "--only",
        "git-commit",
    ])

    assert args.target == "both"
    assert args.only == ["ai-health", "git-commit"]


def test_parser_parses_reindex_queue_prune_stale():
    parser = cli.build_parser()
    args = parser.parse_args(["reindex-queue", "prune-stale", "demo-proj",
                              "--kind", "chroma", "--older-than-sec", "600", "--force-file"])
    assert args.cmd == "reindex-queue"
    assert args.action == "prune-stale"
    assert args.project == "demo-proj"
    assert args.kind == "chroma"
    assert args.older_than_sec == 600
    assert args.force_file is True


def test_parser_parses_reindex_queue_drain_once():
    parser = cli.build_parser()
    args = parser.parse_args(["reindex-queue", "drain-once"])
    assert args.cmd == "reindex-queue"
    assert args.action == "drain-once"


def test_parser_parses_reindex_queue_worker_short_lived():
    parser = cli.build_parser()
    args = parser.parse_args(["reindex-queue", "worker", "--idle-exit-sec", "30", "--heartbeat-sec", "2"])
    assert args.cmd == "reindex-queue"
    assert args.action == "worker"
    assert args.idle_exit_sec == 30
    assert args.heartbeat_sec == 2


def test_parser_parses_graph_audit_all_json():
    parser = cli.build_parser()
    args = parser.parse_args(["graph", "audit", "--all", "--json"])
    assert args.cmd == "graph"
    assert args.action == "audit"
    assert args.all is True
    assert args.json is True


def test_parser_parses_runtime_verify_with_explicit_root():
    parser = cli.build_parser()
    release_id = "a" * 64

    args = parser.parse_args(
        ["runtime", "verify", release_id, "--runtime-root", "/srv/codev-runtime"]
    )

    assert args.cmd == "runtime"
    assert args.runtime_action == "verify"
    assert args.release_id == release_id
    assert str(args.runtime_root).replace("\\", "/") == "/srv/codev-runtime"
    assert callable(args.func)


def test_parser_parses_runtime_deploy_plan():
    parser = cli.build_parser()

    args = parser.parse_args(
        ["runtime", "deploy", "--plan", "/srv/codev-artifacts/deployment-plan.json"]
    )

    assert args.cmd == "runtime"
    assert args.runtime_action == "deploy"
    assert str(args.plan).replace("\\", "/") == "/srv/codev-artifacts/deployment-plan.json"
    assert callable(args.func)


def test_parser_parses_runtime_managed_config_bootstrap():
    parser = cli.build_parser()

    args = parser.parse_args(
        ["runtime", "bootstrap-managed-config", "--service-user", "worker", "--yes"]
    )

    assert args.runtime_action == "bootstrap-managed-config"
    assert args.service_user == "worker"
    assert args.yes is True
    assert callable(args.func)


def test_parser_parses_runtime_publish_staged_access():
    parser = cli.build_parser()
    release_id = "a" * 64

    args = parser.parse_args(
        [
            "runtime",
            "publish-staged-access",
            release_id,
            "--service-user",
            "worker",
            "--yes",
            "--runtime-root",
            "/var/lib/codev-platform/runtime",
        ]
    )

    assert args.runtime_action == "publish-staged-access"
    assert args.release_id == release_id
    assert args.service_user == "worker"
    assert args.yes is True
    assert callable(args.func)


def test_parser_parses_runtime_promote_bootstrap():
    parser = cli.build_parser()
    target = "a" * 64
    rollback = "b" * 64

    args = parser.parse_args(
        [
            "runtime",
            "promote",
            "--target-release",
            target,
            "--rollback-anchor",
            rollback,
            "--service-user",
            "worker",
            "--runtime-root",
            "/var/lib/codev-platform/runtime",
        ]
    )

    assert args.runtime_action == "promote"
    assert args.target_release == target
    assert args.rollback_anchor == rollback
    assert callable(args.func)


def test_parser_parses_runtime_promote_daily_and固定候选根():
    parser = cli.build_parser()
    target = "a" * 40

    args = parser.parse_args(
        [
            "runtime",
            "promote",
            "--target-revision",
            target,
            "--repo",
            "/home/worker/work/codev-platform",
            "--service-user",
            "worker",
            "--runtime-root",
            "/var/lib/codev-platform/runtime",
        ]
    )

    assert args.runtime_action == "promote"
    assert args.target_revision == target
    assert not hasattr(args, "candidate_root")


def test_each_subcommand_has_func():
    parser = cli.build_parser()
    for name in ("health", "reindex", "post-commit", "dirty-check",
                 "install-hooks", "wait-for-reindex", "init", "validate"):
        args = _parse_minimal(parser, name)
        assert hasattr(args, "func") and callable(args.func)


def _parse_minimal(parser, name):
    # validate requires a positional; others parse with just the subcommand
    extra = ["x"] if name == "validate" else []
    return parser.parse_args([name, *extra])
