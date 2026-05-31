"""Tests for `codev-platform memory <init-db|doctor>` (ops.memory_db).

不连真 DB。覆盖: dsn 未配 / psycopg 缺失 两条优雅退出分支 + argparse 路由。
环境约束: 平台 venv 无 psycopg / 无 PG server, 这些分支必须不抛异常、不自动装、退非 0。
"""
from __future__ import annotations

import argparse

import codev_platform.cli as cli
import codev_platform.ops.memory_db as mdb


# ---- argparse 路由 ----

def _build():
    return cli.build_parser()


def test_memory_subcommand_registered():
    parser = _build()
    for action in parser._actions:
        if getattr(action, "choices", None) and "init" in action.choices:
            assert "memory" in action.choices
            return
    raise AssertionError("no subparsers action found")


def test_parser_routes_init_db():
    args = _build().parse_args(["memory", "init-db"])
    assert args.cmd == "memory"
    assert args.action == "init-db"
    assert callable(args.func)


def test_parser_routes_doctor():
    args = _build().parse_args(["memory", "doctor"])
    assert args.cmd == "memory"
    assert args.action == "doctor"
    assert callable(args.func)


# ---- dsn 未配分支(不抛, 退非 0)----

def _ns(action):
    return argparse.Namespace(action=action)


def test_init_db_no_dsn(monkeypatch):
    monkeypatch.setattr(mdb, "load_config", lambda: {}, raising=False)
    # _require_dsn 走 core.config.get(cfg, "memory.pg_dsn"); 空 cfg → None
    rc = mdb.cmd_memory(_ns("init-db"))
    assert rc != 0


def test_doctor_no_dsn(monkeypatch):
    # dsn 未配但仍应继续检查 psycopg, 最终退非 0(无 dsn 不能体检表)
    monkeypatch.setattr(mdb, "load_config", lambda: {}, raising=False)
    rc = mdb.cmd_memory(_ns("doctor"))
    assert rc != 0


# ---- psycopg 缺失分支(monkeypatch _try_import_store → None, 优雅退出)----

def test_init_db_psycopg_missing(monkeypatch):
    monkeypatch.setattr(mdb, "load_config",
                        lambda: {"memory": {"pg_dsn": "postgresql://x/db"}}, raising=False)
    monkeypatch.setattr(mdb, "_try_import_store", lambda: None)
    rc = mdb.cmd_memory(_ns("init-db"))
    assert rc != 0  # 不抛异常, 优雅退出


def test_doctor_psycopg_missing(monkeypatch):
    monkeypatch.setattr(mdb, "load_config",
                        lambda: {"memory": {"pg_dsn": "postgresql://x/db"}}, raising=False)
    monkeypatch.setattr(mdb, "_try_import_store", lambda: None)
    rc = mdb.cmd_memory(_ns("doctor"))
    assert rc != 0


def test_unknown_action(monkeypatch):
    monkeypatch.setattr(mdb, "load_config", lambda: {}, raising=False)
    assert mdb.cmd_memory(_ns("bogus")) != 0
