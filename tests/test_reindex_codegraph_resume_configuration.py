"""CodeGraph 受管恢复配置维护编排测试。"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest


_TARGET = "a" * 40
_RUNTIME_REVISION = "b" * 40


def _context(tmp_path: Path):
    from codev_platform.core.runtime_interpreter import ReleaseInterpreterIdentity
    from codev_platform.ops.reindex_codegraph_resume_context import CodegraphResumeContext

    repository = tmp_path / "repo"
    repository.mkdir()
    return CodegraphResumeContext(
        project_id="demo",
        target_commit=_TARGET,
        runtime_release=ReleaseInterpreterIdentity(
            runtime_revision=_RUNTIME_REVISION,
            release_id="c" * 64,
            interpreter_path="/release/venv/bin/python",
        ),
        config_path=(tmp_path / "overlay.json").resolve(),
        config_digest="b" * 64,
        data_root=(tmp_path / "data").resolve(),
        manifest_path=(tmp_path / "data" / "index_manifest.sqlite").resolve(),
        repositories=(repository.resolve(),),
        health_url="http://127.0.0.1:18091/healthz",
        service_environment_path=(tmp_path / "service.env").resolve(),
    )


def _ports(
    events: list[object],
    context,
    *,
    permitted: bool = True,
    fail_at: str | None = None,
    fail_prepare: bool = False,
):
    from codev_platform.ops.reindex_codegraph_resume_configuration import (
        CodegraphResumeConfigurationPorts,
    )

    def stage(name: str) -> None:
        events.append(name)
        if name == fail_at:
            raise RuntimeError(name)

    @contextmanager
    def permit():
        events.append("permit-enter")
        try:
            yield permitted
        finally:
            events.append("permit-exit")

    def install(**kwargs: object) -> None:
        events.append(("install", kwargs))
        if fail_at == "install":
            raise RuntimeError("install")

    def prepare() -> None:
        events.append("prepare")
        if fail_prepare:
            raise RuntimeError("prepare")

    return CodegraphResumeConfigurationPorts(
        resolve_context=lambda _project, _target: stage("context") or context,
        maintenance_permit=permit,
        inspect_maintenance=lambda: stage("inspect"),
        prepare_maintenance=prepare,
        install_units=install,
    )


def test_配置仅在维护许可内经两次证明后安装(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume_configuration import (
        configure_codegraph_resume,
    )

    context = _context(tmp_path)
    events: list[object] = []

    configure_codegraph_resume(
        project_id="demo",
        target_commit=_TARGET,
        ports=_ports(events, context),
    )

    assert events == [
        "context",
        "permit-enter",
        "inspect",
        (
            "install",
            {
                "config_path": context.config_path,
                "data_root": context.data_root,
                "config_digest": context.config_digest,
                "service_environment_path": context.service_environment_path,
            },
        ),
        "inspect",
        "permit-exit",
    ]


def test_未取得维护许可时不得检查或写入配置(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume_configuration import (
        CodegraphResumeConfigurationError,
        configure_codegraph_resume,
    )

    events: list[object] = []
    with pytest.raises(CodegraphResumeConfigurationError, match="维护窗口未证明"):
        configure_codegraph_resume(
            project_id="demo",
            target_commit=_TARGET,
            ports=_ports(events, _context(tmp_path), permitted=False),
        )

    assert events == ["context", "permit-enter", "permit-exit"]


def test_维护前置证明失败时不得安装或重新准备(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume_configuration import (
        CodegraphResumeConfigurationError,
        configure_codegraph_resume,
    )

    events: list[object] = []
    with pytest.raises(CodegraphResumeConfigurationError, match="维护状态未证明"):
        configure_codegraph_resume(
            project_id="demo",
            target_commit=_TARGET,
            ports=_ports(events, _context(tmp_path), fail_at="inspect"),
        )

    assert events == ["context", "permit-enter", "inspect", "permit-exit"]


def test_安装失败后重新准备并证明维护状态(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume_configuration import (
        CodegraphResumeConfigurationError,
        configure_codegraph_resume,
    )

    events: list[object] = []
    with pytest.raises(CodegraphResumeConfigurationError, match="已回到维护状态"):
        configure_codegraph_resume(
            project_id="demo",
            target_commit=_TARGET,
            ports=_ports(events, _context(tmp_path), fail_at="install"),
        )

    assert events[-3:] == ["permit-exit", "prepare", "inspect"]


def test_安装阶段失败回到维护状态后保留无秘密阶段码(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume_configuration import (
        CodegraphResumeConfigurationError,
        CodegraphResumeConfigurationPorts,
        configure_codegraph_resume,
    )
    from codev_platform.ops.reindex_codegraph_resume_unit_config import (
        CodegraphResumeUnitConfigurationError,
    )

    context = _context(tmp_path)
    events: list[str] = []
    failure = CodegraphResumeUnitConfigurationError("token=不得输出")
    failure.stage = "proof"
    failure.proof_unit = "codev-reindex.service"
    failure.proof_property = "EnvironmentFiles"
    failure.proof_dropin_active = False
    failure.proof_environment_files_reason = "managed_reference"

    @contextmanager
    def permit():
        events.append("permit-enter")
        try:
            yield True
        finally:
            events.append("permit-exit")

    def install(**_kwargs: object) -> None:
        raise failure

    ports = CodegraphResumeConfigurationPorts(
        resolve_context=lambda _project, _target: context,
        maintenance_permit=permit,
        inspect_maintenance=lambda: events.append("inspect"),
        prepare_maintenance=lambda: events.append("prepare"),
        install_units=install,
    )

    with pytest.raises(
        CodegraphResumeConfigurationError,
        match="阶段=同源证明；证明=reindex 环境文件；恢复 drop-in 未生效；原因=恢复快照引用",
    ) as caught:
        configure_codegraph_resume(project_id="demo", target_commit=_TARGET, ports=ports)

    assert "token" not in str(caught.value)
    assert events == ["permit-enter", "inspect", "permit-exit", "prepare", "inspect"]


def test_安装后维护证明与补偿均失败时不得宣称安全(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume_configuration import (
        CodegraphResumeConfigurationError,
        configure_codegraph_resume,
    )

    events: list[object] = []
    with pytest.raises(CodegraphResumeConfigurationError, match="安全状态未证明"):
        configure_codegraph_resume(
            project_id="demo",
            target_commit=_TARGET,
            ports=_ports(
                events,
                _context(tmp_path),
                fail_at="install",
                fail_prepare=True,
            ),
        )

    assert events[-2:] == ["permit-exit", "prepare"]


def test_CLI默认配置入口委托维护编排(monkeypatch: pytest.MonkeyPatch) -> None:
    from codev_platform.ops import reindex_codegraph_resume_configuration as configuration
    from codev_platform.ops import reindex_codegraph_resume_context as context_module
    from codev_platform.ops import reindex_codegraph_resume_unit_config as unit_config
    from codev_platform.ops import reindex_maintenance_cli as cli

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        configuration,
        "configure_codegraph_resume",
        lambda *, project_id, target_commit: calls.append((project_id, target_commit)),
    )
    monkeypatch.setattr(
        context_module,
        "resolve_codegraph_resume_context",
        lambda *_args: pytest.fail("CLI 不得绕过维护编排直接解析上下文"),
    )
    monkeypatch.setattr(
        unit_config,
        "configure_codegraph_resume_units",
        lambda **_kwargs: pytest.fail("CLI 不得绕过维护编排直接写入配置"),
    )

    cli._default_configure_resume_runner("demo", _TARGET)

    assert calls == [("demo", _TARGET)]
