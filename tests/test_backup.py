"""Unit tests for codev_platform.ops.backup pure logic (plan_backup / prune_old).

No PG / subprocess needed: plan_backup is pure config -> item list, prune_old is
pure retention math. dry_run side-effect-free path is checked too.
"""
from __future__ import annotations

from codev_platform.ops import backup as B


def _cfg(pg_dsn=None, data_dir=None, gitea_dir=None):
    return {
        "memory": {"pg_dsn": pg_dsn},
        "data": {"platform_data_dir": data_dir},
        "backup": {"gitea_dir": gitea_dir},
    }


def test_plan_no_pg_when_dsn_empty():
    plan = B.plan_backup(_cfg(pg_dsn=None), "20260601-000000")
    assert not any(i.kind == "pg" for i in plan)


def test_plan_has_pg_when_dsn_set():
    plan = B.plan_backup(_cfg(pg_dsn="postgresql://x"), "20260601-000000")
    pg = [i for i in plan if i.kind == "pg"]
    assert len(pg) == 1
    assert pg[0].dsn == "postgresql://x"


def test_plan_data_subdir_existence(tmp_path):
    # only create chroma + cross_layer.sqlite; codegraph_ext/audit absent
    (tmp_path / "chroma").mkdir()
    (tmp_path / "cross_layer.sqlite").write_text("x")
    plan = B.plan_backup(_cfg(data_dir=str(tmp_path)), "20260601-000000")
    labels = {i.label for i in plan if i.kind == "tree"}
    assert "data-chroma" in labels
    assert "data-cross_layer.sqlite" in labels
    assert "data-codegraph_ext" not in labels
    assert "data-audit" not in labels


def test_plan_gitea_arg_overrides_and_requires_existence(tmp_path):
    gitea = tmp_path / "gitea"
    gitea.mkdir()
    plan = B.plan_backup(_cfg(), "20260601-000000", gitea_dir=str(gitea))
    assert any(i.label == "gitea" for i in plan)
    # non-existent gitea -> skipped
    plan2 = B.plan_backup(_cfg(), "20260601-000000", gitea_dir=str(tmp_path / "nope"))
    assert not any(i.label == "gitea" for i in plan2)


def test_prune_keep_three_of_six():
    dirs = [f"codev-backup-2026060{n}-000000" for n in range(1, 7)]
    victims = B.prune_old(dirs, keep=3)
    assert victims == [
        "codev-backup-20260603-000000",
        "codev-backup-20260602-000000",
        "codev-backup-20260601-000000",
    ]


def test_prune_keep_zero_deletes_nothing():
    dirs = [f"codev-backup-2026060{n}-000000" for n in range(1, 4)]
    assert B.prune_old(dirs, keep=0) == []


def test_prune_ignores_non_backup_names():
    dirs = ["codev-backup-20260601-000000", "codev-backup-20260602-000000",
            "random-dir", "backups", ".keep"]
    victims = B.prune_old(dirs, keep=1)
    assert victims == ["codev-backup-20260601-000000"]
    assert "random-dir" not in victims and "backups" not in victims


def test_dry_run_no_side_effects(tmp_path):
    out = tmp_path / "backups"
    rc = B.run_backup(_cfg(pg_dsn="postgresql://x"), out, keep=7, dry_run=True, gitea_dir=None)
    assert rc == 0
    assert not out.exists()  # dry-run must not create the backup dir


# ---- DSN 密码脱敏(安全红线: manifest / 日志不落明文密码) ----

def test_redact_dsn_masks_password():
    out = B._redact_dsn("postgresql://codev:s3cr3t@db.host:5432/mem")
    assert "s3cr3t" not in out
    assert "***" in out
    assert "codev" in out and "db.host" in out and "5432" in out and "/mem" in out


def test_redact_dsn_no_password_unchanged():
    assert B._redact_dsn("postgresql://codev@db.host/mem") == "postgresql://codev@db.host/mem"
    assert B._redact_dsn(None) is None


def test_dry_run_does_not_print_password(capsys):
    B.run_backup(_cfg(pg_dsn="postgresql://u:topsecret@h/db"), __import__("pathlib").Path("/x"),
                 keep=7, dry_run=True, gitea_dir=None)
    assert "topsecret" not in capsys.readouterr().out


def test_manifest_has_no_plaintext_password(tmp_path):
    # pg_dump 不可用会 fail-soft, 但 manifest 仍写; 断言其中无明文密码。
    out = tmp_path / "backups"
    B.run_backup(_cfg(pg_dsn="postgresql://u:topsecret@h:5432/db"), out,
                 keep=7, dry_run=False, gitea_dir=None)
    manifests = list(out.glob("codev-backup-*/manifest.json"))
    assert manifests, "manifest 应已写"
    text = manifests[0].read_text(encoding="utf-8")
    assert "topsecret" not in text
    assert "***" in text  # dsn 已掩码后入 manifest
