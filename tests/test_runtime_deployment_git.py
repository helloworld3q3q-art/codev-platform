"""生产 Git 首次批准与恢复期不可变历史证明测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform.runtime_deployment_contract import (
    DeploymentPlan,
    RuntimeDeploymentError,
)


def _plan() -> DeploymentPlan:
    return DeploymentPlan(
        schema_version=2,
        target_revision="a" * 40,
        source_repo="/home/helloworld/work/codev-platform",
        runtime_root="/var/lib/codev-platform/runtime",
        requirements_lock="/srv/codev-artifacts/wsl-runtime.lock",
        approved_requirements="/srv/codev-artifacts/wsl-runtime.freeze",
        wheelhouse="/srv/codev-artifacts/wheelhouse",
        candidate_root="/var/tmp/codev-platform-candidates/helloworld",
        config_source="/home/helloworld/.codev-platform/config.json",
        environment_source="/etc/codev-platform/platform.source.env",
        service_user="helloworld",
        project_id="codev-platform",
        require_cuda=True,
    )


def _runner(events: list[tuple[tuple[str, ...], bool]], *, ancestor: bool = True):
    def run(command: tuple[str, ...], network: bool):
        events.append((command, network))
        if command[:4] == ("git", "remote", "get-url", "fuwuqi"):
            return SimpleNamespace(returncode=0, stdout="ssh://git.internal/fuwuqi.git\n")
        if command[:2] == ("git", "fetch"):
            return SimpleNamespace(returncode=0, stdout="")
        if command[:3] == ("git", "rev-parse", "--verify"):
            return SimpleNamespace(returncode=0, stdout=f"{'c' * 40}\n")
        if command[:3] == ("git", "merge-base", "--is-ancestor"):
            return SimpleNamespace(returncode=0 if ancestor else 1, stdout="")
        if command[:3] == ("git", "cat-file", "-e"):
            return SimpleNamespace(returncode=0, stdout="")
        raise AssertionError(f"未知命令：{command}")

    return run


def test_生产目标刷新只允许固定fuwuqi_dev(monkeypatch) -> None:
    import codev_platform.runtime_deployment_git as module

    monkeypatch.setattr(
        module,
        "_require_service_repository",
        lambda path, _user: Path(path),
    )
    events: list[tuple[tuple[str, ...], bool]] = []

    proof = module.refresh_production_git_target(
        Path("/home/helloworld/work/codev-platform"),
        service_user="helloworld",
        target_revision="c" * 40,
        runner=_runner(events),
    )

    assert proof.target_revision == "c" * 40
    assert any(
        command
        == (
            "git",
            "fetch",
            "--quiet",
            "--no-tags",
            "fuwuqi",
            "+refs/heads/dev:refs/remotes/fuwuqi/dev",
        )
        and network is True
        for command, network in events
    )


def test_完整部署目标证明复用生产刷新(monkeypatch) -> None:
    import codev_platform.runtime_deployment_git as module

    observed: dict[str, object] = {}
    expected = module.ProductionGitProof("a" * 40, "b" * 64)

    def refresh(repo, *, service_user, target_revision, runner):
        observed["arguments"] = (repo, service_user, target_revision, runner)
        return expected

    monkeypatch.setattr(module, "refresh_production_git_target", refresh)
    runner = _runner([])

    assert module.verify_production_git_target(_plan(), runner=runner) is expected
    assert observed["arguments"] == (
        Path("/home/helloworld/work/codev-platform"),
        "helloworld",
        "a" * 40,
        runner,
    )


def test_生产目标刷新拒绝远端tip不匹配(monkeypatch) -> None:
    import codev_platform.runtime_deployment_git as module

    monkeypatch.setattr(
        module,
        "_require_service_repository",
        lambda path, _user: Path(path),
    )

    with pytest.raises(RuntimeDeploymentError, match="最新提交"):
        module.refresh_production_git_target(
            Path("/home/helloworld/work/codev-platform"),
            service_user="helloworld",
            target_revision="a" * 40,
            runner=_runner([]),
        )


def test_恢复证明允许fuwuqi_dev在目标提交后继续前进(monkeypatch) -> None:
    import codev_platform.runtime_deployment_git as module

    monkeypatch.setattr(
        module,
        "_require_service_repository",
        lambda path, _user: Path(path),
    )
    events: list[tuple[tuple[str, ...], bool]] = []

    proof = module.verify_production_git_revision(
        _plan(),
        runner=_runner(events),
    )

    assert proof.target_revision == "a" * 40
    assert len(proof.evidence_sha256) == 64
    assert all(network is False for _command, network in events)
    assert not any("fetch" in command for command, _network in events)


def test_恢复证明拒绝已脱离fuwuqi_dev历史的目标(monkeypatch) -> None:
    import codev_platform.runtime_deployment_git as module

    monkeypatch.setattr(
        module,
        "_require_service_repository",
        lambda path, _user: Path(path),
    )

    with pytest.raises(RuntimeDeploymentError, match="历史"):
        module.verify_production_git_revision(
            _plan(),
            runner=_runner([], ancestor=False),
        )
