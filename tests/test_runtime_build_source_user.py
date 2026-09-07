"""root thin release 读取服务账号 Git 源码的显式身份边界。"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from codev_platform.ops import runtime as runtime_cli
from codev_platform import runtime_candidate, runtime_git_snapshot
from codev_platform.runtime_errors import RuntimeBuildError


_REVISION = "a" * 40


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    runtime_cli.register(parser.add_subparsers(dest="command", required=True))
    return parser


def test_runtime_build解析显式服务账号() -> None:
    args = _parser().parse_args(
        [
            "runtime",
            "build",
            "--repo",
            "/srv/source",
            "--out-dir",
            "/srv/candidates",
            "--revision",
            _REVISION,
            "--source-user",
            "svc",
        ]
    )

    assert args.source_user == "svc"


def test_runtime_build将服务账号传给候选构建器() -> None:
    received: list[tuple[object, ...]] = []
    args = argparse.Namespace(
        runtime_action="build",
        repo=Path("/srv/source"),
        out_dir=Path("/srv/candidates"),
        revision=_REVISION,
        source_user="svc",
    )
    ports = argparse.Namespace(
        build_candidate=lambda *items, **kwargs: (
            received.append((*items, kwargs)) or (Path("wheel"), Path("candidate"), "candidate")
        )
    )

    assert runtime_cli._dispatch(args, ports) == "candidate"
    assert received == [
        (Path("/srv/source"), Path("/srv/candidates"), _REVISION, {"source_user": "svc"})
    ]


def test_runtime_build服务账号必须指定精确提交() -> None:
    args = argparse.Namespace(
        runtime_action="build",
        repo=Path("/srv/source"),
        out_dir=Path("/srv/candidates"),
        revision=None,
        source_user="svc",
    )

    with pytest.raises(ValueError, match="精确提交"):
        runtime_cli._dispatch(args, argparse.Namespace())


def test_candidate将服务账号传给Git快照边界(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    seen: list[tuple[str, object]] = []
    monkeypatch.setattr(
        runtime_candidate,
        "_require_repository",
        lambda _repo, *, source_user=None: seen.append(("repo", source_user)) or repo,
    )

    def snapshot(_repo: Path, _revision: str, destination: Path, *, source_user=None) -> Path:
        seen.append(("snapshot", source_user))
        destination.mkdir()
        return destination

    def wheel(_snapshot: Path, output: Path) -> Path:
        result = output / "codev_platform-0.1.0-py3-none-any.whl"
        result.write_bytes(b"wheel")
        return result

    monkeypatch.setattr(runtime_candidate, "materialize_commit", snapshot)
    monkeypatch.setattr(runtime_candidate, "_build_wheel", wheel)

    _wheel, _candidate, built = runtime_candidate.build_candidate(
        repo,
        tmp_path / "candidates",
        _REVISION,
        source_user="svc",
    )

    assert built.runtime_revision == _REVISION
    assert seen == [("repo", "svc"), ("snapshot", "svc")]


def test_git快照将服务账号传给固定archive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    observed: list[object] = []

    def archive(_repo: Path, _revision: str, _path: Path, *, source_user=None) -> None:
        observed.append(source_user)

    monkeypatch.setattr(runtime_git_snapshot, "_write_archive", archive)
    monkeypatch.setattr(
        runtime_git_snapshot,
        "_extract_archive",
        lambda _archive, _destination: None,
    )

    assert (
        runtime_git_snapshot.materialize_commit(
            repo,
            _REVISION,
            tmp_path / "snapshot",
            source_user="svc",
        )
        == tmp_path / "snapshot"
    )
    assert observed == ["svc"]


def test服务账号Git上下文使用固定runuser命令(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    identity = runtime_git_snapshot._ServiceGitIdentity("svc", 1000, "/home/svc")
    checked: list[tuple[Path, object]] = []
    monkeypatch.setattr(
        runtime_git_snapshot,
        "_require_service_git_identity",
        lambda _value: identity,
    )
    monkeypatch.setattr(
        runtime_git_snapshot,
        "_require_service_owned_repository",
        lambda path, value: checked.append((path, value)),
    )

    context = runtime_git_snapshot.git_command_context(repo, source_user="svc")

    assert context.command("archive", "--format=tar", _REVISION) == (
        "/usr/sbin/runuser",
        "--user",
        "svc",
        "--",
        "git",
        "-C",
        str(repo.resolve()),
        "archive",
        "--format=tar",
        _REVISION,
    )
    assert checked == [(repo.resolve(), identity)]
    assert context.environment["HOME"] == "/home/svc"
    assert context.environment["PATH"] == "/usr/bin:/bin"


def test_wheel构建固定离线且不解析依赖(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "source"
    output = tmp_path / "output"
    repo.mkdir()
    output.mkdir()
    captured: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...]) -> None:
        captured.append(command)
        (output / "codev_platform-0.1.0-py3-none-any.whl").write_bytes(b"wheel")

    monkeypatch.setattr(runtime_candidate, "_run_checked", run)

    assert runtime_candidate._build_wheel(repo, output).is_file()
    assert "--no-index" in captured[0]
    assert "--no-deps" in captured[0]
    assert "--no-build-isolation" in captured[0]


def test非特权环境拒绝服务账号Git桥(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    with pytest.raises(runtime_git_snapshot.RuntimeGitSnapshotError, match="服务账号"):
        runtime_git_snapshot.git_command_context(repo, source_user="svc")


def test默认候选构建仍保留原有调用方式(tmp_path: Path) -> None:
    with pytest.raises(RuntimeBuildError, match="应用源码仓"):
        runtime_candidate.build_candidate(tmp_path / "missing", tmp_path / "candidates")
