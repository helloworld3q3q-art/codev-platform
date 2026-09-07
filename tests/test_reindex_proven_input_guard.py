"""受证明 Chroma 输入只能来自冻结仓向量中的 Git tracked 文件。"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from codev_platform.chroma import _discover as discover
from codev_platform.core import config, repos
from codev_platform.core.repo_input_guard import (
    PROVEN_REINDEX_INPUT_ENV,
    TrackedRepoInputGuard,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    _git(root, "init")
    _git(root, "config", "user.name", "测试")
    _git(root, "config", "user.email", "test@example.invalid")
    return root.resolve()


def _commit_all(root: Path) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "测试输入")


def test_guard_accepts_tracked_file_and_rejects_ignored_match(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    docs = root / "docs"
    docs.mkdir()
    tracked = docs / "tracked.md"
    tracked.write_text("tracked", encoding="utf-8")
    (root / ".gitignore").write_text("docs/secret.md\n", encoding="utf-8")
    _commit_all(root)
    ignored = docs / "secret.md"
    ignored.write_text("secret", encoding="utf-8")
    guard = TrackedRepoInputGuard((root,))

    assert guard.validate_files((tracked,)) == (tracked.resolve(),)
    with pytest.raises(ValueError, match="tracked"):
        guard.validate_files((ignored,))


def test_guard_rejects_absolute_external_pattern(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    guard = TrackedRepoInputGuard((root,))

    with pytest.raises(ValueError, match="绝对"):
        guard.validate_external_patterns((str(tmp_path / "secret.md"),))


def test_guard_matches_only_tracked_files_from_registered_extra_repo(tmp_path: Path) -> None:
    main = _repo(tmp_path / "main-root")
    extra = _repo(tmp_path / "extra-root")
    docs = extra / "docs"
    docs.mkdir()
    tracked = docs / "tracked.md"
    tracked.write_text("tracked", encoding="utf-8")
    _commit_all(extra)
    guard = TrackedRepoInputGuard((main, extra))
    pattern = Path(os.path.relpath(extra, main)).as_posix() + "/docs/*.md"

    assert guard.match_external_files(main, (pattern,)) == (tracked.resolve(),)


def test_guard_bounds_pattern_complexity_and_short_circuits_empty_external(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    guard = TrackedRepoInputGuard((root,))

    assert guard.match_external_files(root, ()) == ()
    with pytest.raises(ValueError, match="数量"):
        guard.match_external_files(root, tuple(f"docs/{index}.md" for index in range(129)))
    with pytest.raises(ValueError, match="组件"):
        guard.match_main_files(("/".join(["**"] * 129),))


@pytest.mark.skipif(os.name == "nt", reason="POSIX tracked symlink 逃逸语义")
def test_guard_rejects_tracked_symlink_to_file_outside_repo(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    secret = tmp_path / "secret.md"
    secret.write_text("secret", encoding="utf-8")
    link = root / "docs" / "linked.md"
    link.parent.mkdir()
    link.symlink_to(secret)
    _commit_all(root)
    guard = TrackedRepoInputGuard((root,))

    with pytest.raises(ValueError, match="仓根|符号链接"):
        guard.validate_files((link,))


def _enable_proven_discovery(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    config_path: Path,
) -> None:
    monkeypatch.setenv("CODEV_PLATFORM_CONFIG", str(config_path))
    monkeypatch.setenv("PLATFORM_PROJECT_ID", "demo")
    monkeypatch.setenv(PROVEN_REINDEX_INPUT_ENV, "1")
    cfg = config.load_config()
    specs = [repos.RepoSpec(root=root, tag="", is_main=True, source_project_id="demo")]
    repos.install_runtime_repo_override(cfg, "demo", specs)
    for key, value in config.reindex_config_snapshot_environment(cfg).items():
        monkeypatch.setenv(key, value)
    for key, value in repos.runtime_repo_override_environment(cfg, "demo").items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(discover, "PLATFORM_ROOT", root)
    monkeypatch.setattr(discover, "DOC_PATTERNS", ["docs/**/*.md"])


def test_proven_discovery_rejects_committed_absolute_external_secret_before_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    secret = tmp_path / "service-secret.md"
    secret.write_text("DO-NOT-INDEX", encoding="utf-8")
    index_cfg = root / ".codex" / "index.json"
    index_cfg.parent.mkdir()
    index_cfg.write_text(
        json.dumps({"external_doc_paths": [str(secret)]}),
        encoding="utf-8",
    )
    _commit_all(root)
    machine_config = tmp_path / "config.json"
    machine_config.write_text(
        json.dumps({"projects": {"demo": {"repo_path": str(root)}}}),
        encoding="utf-8",
    )
    _enable_proven_discovery(monkeypatch, root, machine_config)

    with pytest.raises(ValueError, match="绝对"):
        discover.discover_files()


def test_proven_discovery_excludes_ignored_document_match(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    docs = root / "docs"
    docs.mkdir()
    tracked = docs / "tracked.md"
    tracked.write_text("tracked", encoding="utf-8")
    (root / ".gitignore").write_text("docs/secret.md\n", encoding="utf-8")
    _commit_all(root)
    (docs / "secret.md").write_text("DO-NOT-INDEX", encoding="utf-8")
    machine_config = tmp_path / "config.json"
    machine_config.write_text(
        json.dumps({"projects": {"demo": {"repo_path": str(root)}}}),
        encoding="utf-8",
    )
    _enable_proven_discovery(monkeypatch, root, machine_config)

    assert discover.discover_files() == [tracked.resolve()]


def test_proven_discovery_rejects_invalid_committed_index_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    index_cfg = root / ".codex" / "index.json"
    index_cfg.parent.mkdir()
    index_cfg.write_text("{broken", encoding="utf-8")
    _commit_all(root)
    machine_config = tmp_path / "config.json"
    machine_config.write_text(
        json.dumps({"projects": {"demo": {"repo_path": str(root)}}}),
        encoding="utf-8",
    )
    _enable_proven_discovery(monkeypatch, root, machine_config)

    with pytest.raises(ValueError, match="无法解析"):
        discover.discover_files()


def test_proven_discovery_rejects_escaping_external_glob_before_filesystem_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    index_cfg = root / ".codex" / "index.json"
    index_cfg.parent.mkdir()
    index_cfg.write_text(
        json.dumps({"external_doc_paths": ["../**/*"]}),
        encoding="utf-8",
    )
    _commit_all(root)
    machine_config = tmp_path / "config.json"
    machine_config.write_text(
        json.dumps({"projects": {"demo": {"repo_path": str(root)}}}),
        encoding="utf-8",
    )
    _enable_proven_discovery(monkeypatch, root, machine_config)
    monkeypatch.setattr(
        "glob.glob",
        lambda *_args, **_kwargs: pytest.fail("逃逸 pattern 不得触发文件系统 glob"),
    )

    with pytest.raises(ValueError, match="搜索锚|逃逸"):
        discover.discover_files()


def test_proven_discovery_preserves_archive_exclusion_and_incident_whitelist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    archive = root / "docs" / "archive"
    incidents = archive / "incidents"
    incidents.mkdir(parents=True)
    hidden = archive / "old.md"
    kept = incidents / "incident.md"
    hidden.write_text("old", encoding="utf-8")
    kept.write_text("incident", encoding="utf-8")
    _commit_all(root)
    machine_config = tmp_path / "config.json"
    machine_config.write_text(
        json.dumps({"projects": {"demo": {"repo_path": str(root)}}}),
        encoding="utf-8",
    )
    _enable_proven_discovery(monkeypatch, root, machine_config)

    assert discover.discover_files() == [kept.resolve()]
