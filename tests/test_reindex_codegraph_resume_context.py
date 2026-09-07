"""CodeGraph 受控恢复的单一配置快照与仓集合解析测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


_TARGET = "a" * 40
_RUNTIME_REVISION = "b" * 40
_SERVICE_ENVIRONMENT_PATH = Path("/etc/codev-platform/platform.env")


@pytest.fixture(autouse=True)
def _固定恢复运行时版本(monkeypatch: pytest.MonkeyPatch) -> None:
    from codev_platform.core.runtime_interpreter import ReleaseInterpreterIdentity
    from codev_platform.ops import reindex_codegraph_resume_context as context_module

    monkeypatch.setattr(
        context_module,
        "current_release_interpreter_identity",
        lambda: ReleaseInterpreterIdentity(
            runtime_revision=_RUNTIME_REVISION,
            release_id="c" * 64,
            interpreter_path="/release/venv/bin/python",
        ),
    )


def test_服务环境文件使用POSIX绝对路径语义() -> None:
    """即使生成端运行在 Windows，也应按目标 systemd 的 POSIX 路径解析。"""
    from codev_platform.ops.reindex_codegraph_resume_context import (
        _resolve_service_environment_path,
    )

    path = _resolve_service_environment_path(
        {"systemd": {"env_file": "/etc/codev-platform/platform.env"}}
    )

    assert path is not None
    assert path.as_posix() == "/etc/codev-platform/platform.env"


def _write_overlay(
    tmp_path: Path,
    *,
    data_root: Path | None,
    main_repo: Path,
    extra_repo: Path | None = None,
    codegraph_port: int = 18091,
    service_environment_path: Path | None = None,
) -> Path:
    project: dict[str, object] = {"repo_path": str(main_repo)}
    if extra_repo is not None:
        project["extra_repos"] = [str(extra_repo)]
    payload = {
        "data": {"platform_data_dir": None if data_root is None else str(data_root)},
        "mcp": {"codegraph_sse_port": codegraph_port},
        "projects": {"demo": project},
        "systemd": {
            "env_file": (
                None if service_environment_path is None else service_environment_path.as_posix()
            )
        },
    }
    path = tmp_path / "resume-config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_恢复上下文只用显式overlay解析同一数据根manifest与多仓集合(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.ops import reindex_codegraph_resume_context as context_module

    data = tmp_path / "platform-data"
    main = tmp_path / "main"
    extra = tmp_path / "extra"
    main.mkdir()
    extra.mkdir()
    overlay = _write_overlay(
        tmp_path,
        data_root=data,
        main_repo=main,
        extra_repo=extra,
        codegraph_port=19091,
        service_environment_path=_SERVICE_ENVIRONMENT_PATH,
    )
    monkeypatch.setenv("CODEV_PLATFORM_CONFIG", str(overlay))
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(data))
    context = context_module.resolve_codegraph_resume_context("demo", _TARGET)

    assert context.project_id == "demo"
    assert context.target_commit == _TARGET
    assert context.runtime_revision == _RUNTIME_REVISION
    assert context.runtime_release.release_id == "c" * 64
    assert context.config_path == overlay.resolve()
    assert context.data_root == data.resolve()
    assert context.manifest_path == data.resolve() / "index_manifest.sqlite"
    assert context.repositories == (main.resolve(), extra.resolve())
    assert context.health_url == "http://127.0.0.1:19091/healthz"
    assert context.service_environment_path == _SERVICE_ENVIRONMENT_PATH


def test_恢复上下文拒绝环境数据根覆盖overlay配置(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.ops.reindex_codegraph_resume_context import (
        CodegraphResumeContextError,
        resolve_codegraph_resume_context,
    )

    main = tmp_path / "main"
    main.mkdir()
    overlay = _write_overlay(
        tmp_path,
        data_root=tmp_path / "overlay-data",
        main_repo=main,
    )
    monkeypatch.setenv("CODEV_PLATFORM_CONFIG", str(overlay))
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path / "other-data"))

    with pytest.raises(CodegraphResumeContextError, match="数据根"):
        resolve_codegraph_resume_context("demo", _TARGET)


@pytest.mark.parametrize(
    "overlay_value",
    ["relative-config.json", ""],
)
def test_恢复上下文拒绝缺失或非绝对配置覆盖(
    overlay_value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.ops.reindex_codegraph_resume_context import (
        CodegraphResumeContextError,
        resolve_codegraph_resume_context,
    )

    if overlay_value:
        monkeypatch.setenv("CODEV_PLATFORM_CONFIG", overlay_value)
    else:
        monkeypatch.delenv("CODEV_PLATFORM_CONFIG", raising=False)

    with pytest.raises(CodegraphResumeContextError, match="配置覆盖"):
        resolve_codegraph_resume_context("demo", _TARGET)


def test_恢复上下文拒绝没有绝对数据根或仓集合(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.ops.reindex_codegraph_resume_context import (
        CodegraphResumeContextError,
        resolve_codegraph_resume_context,
    )

    main = tmp_path / "main"
    main.mkdir()
    overlay = _write_overlay(tmp_path, data_root=None, main_repo=main)
    monkeypatch.setenv("CODEV_PLATFORM_CONFIG", str(overlay))
    monkeypatch.delenv("PLATFORM_DATA_DIR", raising=False)

    with pytest.raises(CodegraphResumeContextError, match="数据根"):
        resolve_codegraph_resume_context("demo", _TARGET)


def test_恢复上下文拒绝缺失显式环境数据根(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.ops.reindex_codegraph_resume_context import (
        CodegraphResumeContextError,
        resolve_codegraph_resume_context,
    )

    main = tmp_path / "main"
    main.mkdir()
    overlay = _write_overlay(
        tmp_path,
        data_root=tmp_path / "platform-data",
        main_repo=main,
    )
    monkeypatch.setenv("CODEV_PLATFORM_CONFIG", str(overlay))
    monkeypatch.delenv("PLATFORM_DATA_DIR", raising=False)

    with pytest.raises(CodegraphResumeContextError, match="环境数据根"):
        resolve_codegraph_resume_context("demo", _TARGET)


def test_恢复上下文拒绝运行时仓覆盖环境(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.ops.reindex_codegraph_resume_context import (
        CodegraphResumeContextError,
        resolve_codegraph_resume_context,
    )

    data = tmp_path / "platform-data"
    main = tmp_path / "main"
    main.mkdir()
    overlay = _write_overlay(tmp_path, data_root=data, main_repo=main)
    monkeypatch.setenv("CODEV_PLATFORM_CONFIG", str(overlay))
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(data))
    monkeypatch.setenv("CODEV_REINDEX_REPO_OVERRIDE", "不可信覆盖")

    with pytest.raises(CodegraphResumeContextError, match="仓覆盖"):
        resolve_codegraph_resume_context("demo", _TARGET)


def test_恢复上下文检测配置覆盖在读取期间被替换(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.ops import reindex_codegraph_resume_context as context_module

    data = tmp_path / "platform-data"
    main = tmp_path / "main"
    main.mkdir()
    overlay = _write_overlay(tmp_path, data_root=data, main_repo=main)
    monkeypatch.setenv("CODEV_PLATFORM_CONFIG", str(overlay))
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(data))
    original_loader = context_module.load_config

    def _替换后读取():
        replacement = tmp_path / "replacement-config.json"
        replacement.write_text(overlay.read_text(encoding="utf-8"), encoding="utf-8")
        replacement.replace(overlay)
        return original_loader()

    monkeypatch.setattr(context_module, "load_config", _替换后读取)

    with pytest.raises(context_module.CodegraphResumeContextError, match="已变化"):
        context_module.resolve_codegraph_resume_context("demo", _TARGET)


def test_恢复上下文只加载一次配置快照并复用到健康地址(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.ops import reindex_codegraph_resume_context as context_module

    data = tmp_path / "platform-data"
    main = tmp_path / "main"
    main.mkdir()
    overlay = _write_overlay(tmp_path, data_root=data, main_repo=main)
    monkeypatch.setenv("CODEV_PLATFORM_CONFIG", str(overlay))
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(data))
    original_loader = context_module.load_config
    calls = 0

    def load_once():
        nonlocal calls
        calls += 1
        return original_loader()

    monkeypatch.setattr(context_module, "load_config", load_once)

    context_module.resolve_codegraph_resume_context("demo", _TARGET)

    assert calls == 1
