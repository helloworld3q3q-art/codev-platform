"""CodeGraph 恢复受管配置文件安装测试。"""

from __future__ import annotations

from pathlib import Path

import pytest


_DIGEST = "a" * 64


def _原像(content: bytes, *, mode: int = 0o600, gid: int = 0):
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        RootOwnedRegularFileSnapshot,
    )

    return RootOwnedRegularFileSnapshot(content=content, mode=mode, uid=0, gid=gid)


def _bind_paths(monkeypatch: pytest.MonkeyPatch, module, tmp_path: Path) -> tuple[Path, Path, Path]:
    environment = tmp_path / "etc" / "codev-platform" / "resume.env"
    reindex = tmp_path / "systemd" / "codev-reindex.service.d" / "20-resume.conf"
    codegraph = tmp_path / "systemd" / "codev-mcp-codegraph.service.d" / "20-resume.conf"
    monkeypatch.setattr(module, "RESUME_ENVIRONMENT_FILE", environment)
    monkeypatch.setattr(module, "REINDEX_RESUME_DROPIN", reindex)
    monkeypatch.setattr(module, "CODEGRAPH_RESUME_DROPIN", codegraph)
    return environment, reindex, codegraph


def test_安装受管恢复配置时清空旧来源后追加受管环境文件(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform.ops import reindex_codegraph_resume_unit_config as module

    environment, reindex_dropin, codegraph_dropin = _bind_paths(monkeypatch, module, tmp_path)
    overlay = tmp_path / "overlay.json"
    overlay.write_text("{}", encoding="utf-8")
    data_root = tmp_path / "data"
    service_environment = (tmp_path / "service.env").resolve()
    events: list[object] = []

    def write(path: Path, content: bytes, mode: int) -> None:
        events.append(("write", path, content, mode))

    def reload() -> None:
        events.append("reload")

    def proof(**kwargs) -> None:
        events.append(("proof", kwargs))

    module.configure_codegraph_resume_units(
        config_path=overlay.resolve(),
        data_root=data_root.resolve(),
        config_digest=_DIGEST,
        service_environment_path=service_environment,
        platform_name="linux",
        effective_user_id=lambda: 0,
        file_writer=write,
        file_snapshot_reader=lambda _path: None,
        systemd_reloader=reload,
        configuration_proof=proof,
    )

    rendered_dropin = module.render_codegraph_resume_dropin_content(service_environment)

    assert events[0] == (
        "write",
        codegraph_dropin,
        (
            "[Service]\n"
            "EnvironmentFile=\n"
            f"EnvironmentFile={service_environment.as_posix()}\n"
            f"EnvironmentFile={environment.as_posix()}\n"
        ).encode(),
        0o644,
    )
    assert events[0][2] == rendered_dropin
    assert events[1] == (
        "write",
        reindex_dropin,
        (
            "[Service]\n"
            "EnvironmentFile=\n"
            f"EnvironmentFile={service_environment.as_posix()}\n"
            f"EnvironmentFile={environment.as_posix()}\n"
        ).encode(),
        0o644,
    )
    assert events[2] == (
        "write",
        environment,
        (
            f"CODEV_PLATFORM_CONFIG={overlay.resolve().as_posix()}\n"
            f"PLATFORM_DATA_DIR={data_root.resolve().as_posix()}\n"
            f"CODEV_REINDEX_CONFIG_SHA256={_DIGEST}\n"
        ).encode(),
        0o600,
    )
    assert events[3] == "reload"
    assert events[4] == (
        "proof",
        {
            "config_path": overlay.resolve(),
            "data_root": data_root.resolve(),
            "config_digest": _DIGEST,
            "managed_environment_path": environment,
        },
    )


def test_安装受管恢复配置拒绝非root调用且不写任何文件(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform.ops import reindex_codegraph_resume_unit_config as module

    _bind_paths(monkeypatch, module, tmp_path)
    overlay = tmp_path / "overlay.json"
    overlay.write_text("{}", encoding="utf-8")

    with pytest.raises(module.CodegraphResumeUnitConfigurationError):
        module.configure_codegraph_resume_units(
            config_path=overlay.resolve(),
            data_root=(tmp_path / "data").resolve(),
            config_digest=_DIGEST,
            platform_name="linux",
            effective_user_id=lambda: 1000,
            file_writer=lambda *_args: pytest.fail("非 root 不得写入配置"),
        )


def test_安装受管恢复配置拒绝无法安全写入systemd环境的值(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform.ops import reindex_codegraph_resume_unit_config as module

    _bind_paths(monkeypatch, module, tmp_path)
    overlay = tmp_path / "overlay with space.json"
    overlay.write_text("{}", encoding="utf-8")

    with pytest.raises(module.CodegraphResumeUnitConfigurationError):
        module.configure_codegraph_resume_units(
            config_path=overlay.resolve(),
            data_root=(tmp_path / "data").resolve(),
            config_digest=_DIGEST,
            platform_name="linux",
            effective_user_id=lambda: 0,
            file_writer=lambda *_args: pytest.fail("不安全值不得写入配置"),
        )


def test_受管恢复配置写入失败时恢复三个文件原像(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform.ops import reindex_codegraph_resume_unit_config as module

    environment, reindex_dropin, codegraph_dropin = _bind_paths(monkeypatch, module, tmp_path)
    overlay = tmp_path / "overlay.json"
    overlay.write_text("{}", encoding="utf-8")
    original = {
        environment: _原像(b"old-environment\n", mode=0o640, gid=321),
        reindex_dropin: _原像(b"old-reindex\n", mode=0o644, gid=654),
        codegraph_dropin: _原像(b"old-codegraph\n", mode=0o600, gid=987),
    }
    events: list[object] = []

    def write(path: Path, content: bytes, mode: int) -> None:
        events.append(("write", path, content, mode))
        if path == reindex_dropin:
            raise OSError("reindex write failed")

    def restore(path: Path, file_snapshot) -> None:
        events.append(("restore", path, file_snapshot))

    with pytest.raises(module.CodegraphResumeUnitConfigurationError, match="阶段=写入") as caught:
        module.configure_codegraph_resume_units(
            config_path=overlay.resolve(),
            data_root=(tmp_path / "data").resolve(),
            config_digest=_DIGEST,
            platform_name="linux",
            effective_user_id=lambda: 0,
            file_writer=write,
            file_snapshot_reader=lambda path: original[path],
            file_restorer=restore,
            systemd_reloader=lambda: events.append("reload"),
        )

    assert caught.value.stage == "write"

    assert [item[1] for item in events if item[0] == "write"] == [
        codegraph_dropin,
        reindex_dropin,
    ]
    assert [item[1] for item in events if item[0] == "restore"] == [
        environment,
        reindex_dropin,
        codegraph_dropin,
    ]
    assert [item[2] for item in events if item[0] == "restore"] == [
        original[environment],
        original[reindex_dropin],
        original[codegraph_dropin],
    ]
    assert events[-1] == "reload"


def test_配置证明失败时恢复原像并再次重载systemd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform.ops import reindex_codegraph_resume_unit_config as module

    environment, reindex_dropin, codegraph_dropin = _bind_paths(monkeypatch, module, tmp_path)
    overlay = tmp_path / "overlay.json"
    overlay.write_text("{}", encoding="utf-8")
    original = {
        environment: _原像(b"old-env\n", mode=0o640, gid=321),
        reindex_dropin: None,
        codegraph_dropin: None,
    }
    events: list[object] = []

    def write(path: Path, content: bytes, mode: int) -> None:
        events.append(("write", path, content, mode))

    def restore(path: Path, file_snapshot) -> None:
        events.append(("restore", path, file_snapshot))

    with pytest.raises(
        module.CodegraphResumeUnitConfigurationError, match="阶段=同源证明"
    ) as caught:
        module.configure_codegraph_resume_units(
            config_path=overlay.resolve(),
            data_root=(tmp_path / "data").resolve(),
            config_digest=_DIGEST,
            platform_name="linux",
            effective_user_id=lambda: 0,
            file_writer=write,
            file_snapshot_reader=lambda path: original[path],
            file_restorer=restore,
            systemd_reloader=lambda: events.append("reload"),
            configuration_proof=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("proof")),
        )

    assert caught.value.stage == "proof"

    assert events.count("reload") == 2
    assert [item[1] for item in events if item[0] == "restore"] == [
        environment,
        reindex_dropin,
        codegraph_dropin,
    ]


def test_配置证明失败时仅公开固定证明位置(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform.ops import reindex_codegraph_resume_unit_config as module
    from codev_platform.ops.reindex_codegraph_resume_config_proof import (
        CodegraphResumeConfigurationError,
    )

    environment, reindex_dropin, codegraph_dropin = _bind_paths(monkeypatch, module, tmp_path)
    overlay = tmp_path / "overlay.json"
    overlay.write_text("{}", encoding="utf-8")
    original = {
        environment: _原像(b"old-env\n", mode=0o640, gid=321),
        reindex_dropin: None,
        codegraph_dropin: None,
    }
    proof_error = CodegraphResumeConfigurationError("token=不得输出")
    proof_error.unit = "codev-reindex.service"
    proof_error.property_name = "EnvironmentFiles"
    proof_error.dropin_active = False
    proof_error.environment_files_reason = "managed_reference"

    with pytest.raises(
        module.CodegraphResumeUnitConfigurationError,
        match="阶段=同源证明；证明=reindex 环境文件；恢复 drop-in 未生效；原因=恢复快照引用",
    ) as caught:
        module.configure_codegraph_resume_units(
            config_path=overlay.resolve(),
            data_root=(tmp_path / "data").resolve(),
            config_digest=_DIGEST,
            platform_name="linux",
            effective_user_id=lambda: 0,
            file_writer=lambda *_args: None,
            file_snapshot_reader=lambda path: original[path],
            file_restorer=lambda *_args: None,
            systemd_reloader=lambda: None,
            configuration_proof=lambda **_kwargs: (_ for _ in ()).throw(proof_error),
        )

    assert "token" not in str(caught.value)


def test_重载失败时保留固定阶段码并恢复原像(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform.ops import reindex_codegraph_resume_unit_config as module

    environment, reindex_dropin, codegraph_dropin = _bind_paths(monkeypatch, module, tmp_path)
    overlay = tmp_path / "overlay.json"
    overlay.write_text("{}", encoding="utf-8")
    original = {
        environment: _原像(b"old-env\n", mode=0o640, gid=321),
        reindex_dropin: None,
        codegraph_dropin: None,
    }
    events: list[object] = []

    def write(path: Path, content: bytes, mode: int) -> None:
        events.append(("write", path, content, mode))

    def restore(path: Path, file_snapshot) -> None:
        events.append(("restore", path, file_snapshot))

    with pytest.raises(module.CodegraphResumeUnitConfigurationError, match="阶段=重载") as caught:
        module.configure_codegraph_resume_units(
            config_path=overlay.resolve(),
            data_root=(tmp_path / "data").resolve(),
            config_digest=_DIGEST,
            platform_name="linux",
            effective_user_id=lambda: 0,
            file_writer=write,
            file_snapshot_reader=lambda path: original[path],
            file_restorer=restore,
            systemd_reloader=lambda: (_ for _ in ()).throw(RuntimeError("reload")),
        )

    assert caught.value.stage == "reload"
    assert [item[1] for item in events if item[0] == "restore"] == [
        environment,
        reindex_dropin,
        codegraph_dropin,
    ]


def test_受管恢复配置回滚失败时不得宣称安全(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform.ops import reindex_codegraph_resume_unit_config as module

    environment, reindex_dropin, codegraph_dropin = _bind_paths(monkeypatch, module, tmp_path)
    overlay = tmp_path / "overlay.json"
    overlay.write_text("{}", encoding="utf-8")

    with pytest.raises(module.CodegraphResumeUnitConfigurationError, match="安全状态未证明"):
        module.configure_codegraph_resume_units(
            config_path=overlay.resolve(),
            data_root=(tmp_path / "data").resolve(),
            config_digest=_DIGEST,
            platform_name="linux",
            effective_user_id=lambda: 0,
            file_writer=lambda *_args: (_ for _ in ()).throw(RuntimeError("write")),
            file_snapshot_reader=lambda _path: None,
            file_restorer=lambda *_args: (_ for _ in ()).throw(RuntimeError("rollback")),
        )


def test_受管恢复配置补偿首项失败仍尝试恢复其余文件(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """单个恢复动作失败不能短路其余受管文件的补偿。"""
    from codev_platform.ops import reindex_codegraph_resume_unit_config as module

    environment, reindex_dropin, codegraph_dropin = _bind_paths(monkeypatch, module, tmp_path)
    overlay = tmp_path / "overlay.json"
    overlay.write_text("{}", encoding="utf-8")
    originals = {
        environment: _原像(b"old-environment\n", mode=0o640, gid=321),
        reindex_dropin: _原像(b"old-reindex\n", mode=0o644, gid=654),
        codegraph_dropin: _原像(b"old-codegraph\n", mode=0o600, gid=987),
    }
    restored_paths: list[Path] = []
    reads: list[Path] = []
    reloads: list[str] = []

    def restore(path: Path, _file_snapshot) -> None:
        restored_paths.append(path)
        if path == environment:
            raise RuntimeError("首个补偿失败")

    def read_snapshot(path: Path):
        reads.append(path)
        return originals[path]

    with pytest.raises(module.CodegraphResumeUnitConfigurationError, match="安全状态未证明"):
        module.configure_codegraph_resume_units(
            config_path=overlay.resolve(),
            data_root=(tmp_path / "data").resolve(),
            config_digest=_DIGEST,
            platform_name="linux",
            effective_user_id=lambda: 0,
            file_writer=lambda *_args: (_ for _ in ()).throw(RuntimeError("write")),
            file_snapshot_reader=read_snapshot,
            file_restorer=restore,
            systemd_reloader=lambda: reloads.append("reload"),
        )

    assert restored_paths == [environment, reindex_dropin, codegraph_dropin]
    assert reloads == ["reload"]
    assert reads == [
        codegraph_dropin,
        reindex_dropin,
        environment,
        codegraph_dropin,
        reindex_dropin,
        environment,
    ]


def test_回滚最终证明同时校验原像内容权限与完整属主(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform.ops import reindex_codegraph_resume_unit_config as module

    environment, reindex_dropin, codegraph_dropin = _bind_paths(monkeypatch, module, tmp_path)
    overlay = tmp_path / "overlay.json"
    overlay.write_text("{}", encoding="utf-8")
    originals = {
        environment: _原像(b"old-environment\n", mode=0o640, gid=321),
        reindex_dropin: _原像(b"old-reindex\n", mode=0o644, gid=654),
        codegraph_dropin: _原像(b"old-codegraph\n", mode=0o600, gid=987),
    }
    current = dict(originals)
    reloads: list[str] = []

    def write(path: Path, content: bytes, mode: int) -> None:
        current[path] = _原像(content, mode=mode)

    def restore(path: Path, file_snapshot) -> None:
        if file_snapshot is None:
            current[path] = None
            return
        current[path] = _原像(file_snapshot.content, mode=file_snapshot.mode, gid=0)

    with pytest.raises(module.CodegraphResumeUnitConfigurationError, match="安全状态未证明"):
        module.configure_codegraph_resume_units(
            config_path=overlay.resolve(),
            data_root=(tmp_path / "data").resolve(),
            config_digest=_DIGEST,
            platform_name="linux",
            effective_user_id=lambda: 0,
            file_writer=write,
            file_snapshot_reader=lambda path: current[path],
            file_restorer=restore,
            systemd_reloader=lambda: reloads.append("reload"),
            configuration_proof=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("proof")),
        )

    assert reloads == ["reload", "reload"]
    assert current[environment].content == originals[environment].content
    assert current[environment].mode == originals[environment].mode
    assert current[environment].uid == originals[environment].uid
    assert current[environment].gid != originals[environment].gid


def test_默认原像写入与删除适配器统一委派可信dirfd路径叶子(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform.ops import reindex_codegraph_resume_managed_path as managed
    from codev_platform.ops import reindex_codegraph_resume_unit_config as module

    target = (tmp_path / "etc" / "resume.env").resolve()
    calls: list[object] = []
    original = _原像(b"old\n", mode=0o640, gid=1234)
    monkeypatch.setattr(
        managed,
        "read_optional_root_owned_regular_file_snapshot",
        lambda path, *, max_bytes: calls.append(("read", path, max_bytes)) or original,
    )
    monkeypatch.setattr(
        managed,
        "write_root_owned_regular_file_atomic",
        lambda path, content, *, mode, uid=0, gid=0: calls.append(
            ("write", path, content, mode, uid, gid)
        ),
    )
    monkeypatch.setattr(
        managed,
        "remove_root_owned_regular_file",
        lambda path: calls.append(("remove", path)) or True,
    )

    assert module._default_file_snapshot_reader(target) == original
    module._atomic_file_writer(target, b"new\n", 0o600)
    module._default_file_restorer(target, original)
    module._default_file_restorer(target, None)

    assert calls == [
        ("read", target, module._MAX_MANAGED_FILE_BYTES),
        ("write", target, b"new\n", 0o600, 0, 0),
        ("write", target, b"old\n", 0o640, 0, 1234),
        ("remove", target),
    ]
