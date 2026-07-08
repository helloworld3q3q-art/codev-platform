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
                 "install-hooks", "wait-for-reindex", "reindex-queue"):
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
