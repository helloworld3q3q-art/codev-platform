"""CodeGraph 恢复调用方与两个受管 unit 的配置同源证明测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


_DIGEST = "a" * 64


def _ok(value: str) -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=value + "\n", stderr="敏感 systemd 输出")


def _managed_environment(config: Path, data_root: Path, *, digest: str = _DIGEST) -> bytes:
    return (
        f"CODEV_PLATFORM_CONFIG={config.as_posix()}\n"
        f"PLATFORM_DATA_DIR={data_root.as_posix()}\n"
        f"CODEV_REINDEX_CONFIG_SHA256={digest}\n"
    ).encode()


def _runner(
    *,
    environment: str,
    environment_files: str,
    dropin_paths: str = "",
    overrides: dict[str, str] | None = None,
    calls: list[tuple[str, ...]] | None = None,
):
    values = overrides or {}

    def run(command: tuple[str, ...], **_kwargs) -> SimpleNamespace:
        if calls is not None:
            calls.append(command)
        property_name = command[3]
        if property_name == "--property=Environment":
            return _ok(environment)
        if property_name == "--property=EnvironmentFiles":
            return _ok(environment_files)
        if property_name == "--property=DropInPaths":
            return _ok(dropin_paths)
        if property_name in {
            "--property=UnsetEnvironment",
            "--property=PassEnvironment",
            "--property=PAMName",
        }:
            return _ok(values.get(property_name, ""))
        raise AssertionError("只允许读取受控环境证明字段")

    return run


def _verify(
    tmp_path: Path,
    *,
    environment: str = "PATH=/usr/bin",
    environment_files: str | None = None,
    managed_content: bytes | None = None,
    extra_files: dict[Path, bytes] | None = None,
    dropin_paths: str = "",
    overrides: dict[str, str] | None = None,
    calls: list[tuple[str, ...]] | None = None,
) -> None:
    from codev_platform.ops.reindex_codegraph_resume_config_proof import (
        verify_codegraph_resume_configuration,
    )

    overlay = tmp_path / "overlay.json"
    overlay.write_text("{}", encoding="utf-8")
    data_root = tmp_path / "data"
    managed = tmp_path / "resume.env"
    content_by_path = {
        managed: _managed_environment(overlay.resolve(), data_root.resolve())
        if managed_content is None
        else managed_content,
        **(extra_files or {}),
    }
    files = (
        f"{managed.as_posix()} (ignore_errors=no)"
        if environment_files is None
        else environment_files
    )

    verify_codegraph_resume_configuration(
        config_path=overlay.resolve(),
        data_root=data_root.resolve(),
        config_digest=_DIGEST,
        managed_environment_path=managed,
        platform_name="linux",
        command_runner=_runner(
            environment=environment,
            environment_files=files,
            dropin_paths=dropin_paths,
            overrides=overrides,
            calls=calls,
        ),
        environment_file_reader=lambda path: content_by_path[path],
    )


def test_恢复配置同源证明要求两个固定unit只引用同一受管快照文件(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    _verify(tmp_path, calls=calls)

    assert calls == [
        (
            "systemctl",
            "show",
            "codev-reindex.service",
            "--property=Environment",
            "--value",
        ),
        (
            "systemctl",
            "show",
            "codev-reindex.service",
            "--property=EnvironmentFiles",
            "--value",
        ),
        (
            "systemctl",
            "show",
            "codev-reindex.service",
            "--property=UnsetEnvironment",
            "--value",
        ),
        (
            "systemctl",
            "show",
            "codev-reindex.service",
            "--property=PassEnvironment",
            "--value",
        ),
        (
            "systemctl",
            "show",
            "codev-reindex.service",
            "--property=PAMName",
            "--value",
        ),
        (
            "systemctl",
            "show",
            "codev-mcp-codegraph.service",
            "--property=Environment",
            "--value",
        ),
        (
            "systemctl",
            "show",
            "codev-mcp-codegraph.service",
            "--property=EnvironmentFiles",
            "--value",
        ),
        (
            "systemctl",
            "show",
            "codev-mcp-codegraph.service",
            "--property=UnsetEnvironment",
            "--value",
        ),
        (
            "systemctl",
            "show",
            "codev-mcp-codegraph.service",
            "--property=PassEnvironment",
            "--value",
        ),
        (
            "systemctl",
            "show",
            "codev-mcp-codegraph.service",
            "--property=PAMName",
            "--value",
        ),
    ]


def test_恢复配置同源证明接受systemd多行环境文件输出(tmp_path: Path) -> None:
    managed = tmp_path / "resume.env"
    token_file = tmp_path / "token.env"

    _verify(
        tmp_path,
        environment_files=(
            f"{token_file.as_posix()} (ignore_errors=no)\n{managed.as_posix()} (ignore_errors=no)"
        ),
        extra_files={token_file: b"CODEV_PLATFORM_MCP_TOKEN=test-token\n"},
    )


def test_恢复配置同源证明接受带引号token的可信辅助环境文件(tmp_path: Path) -> None:
    managed = tmp_path / "resume.env"
    token_file = tmp_path / "token.env"

    _verify(
        tmp_path,
        environment_files=(
            f"{token_file.as_posix()} (ignore_errors=no)\n{managed.as_posix()} (ignore_errors=no)"
        ),
        extra_files={token_file: b'CODEV_PLATFORM_MCP_TOKEN="opaque token"\n'},
    )


def test_恢复配置同源证明接受同一root配置覆盖与令牌环境共存(tmp_path: Path) -> None:
    managed = tmp_path / "resume.env"
    token_file = tmp_path / "token.env"
    overlay = (tmp_path / "overlay.json").resolve()

    _verify(
        tmp_path,
        environment_files=(
            f"{token_file.as_posix()} (ignore_errors=no)\n{managed.as_posix()} (ignore_errors=no)"
        ),
        extra_files={
            token_file: (
                f"CODEV_PLATFORM_CONFIG={overlay.as_posix()}\n"
                "CODEV_PLATFORM_MCP_TOKEN=opaque-token\n"
            ).encode()
        },
    )


@pytest.mark.parametrize(
    ("environment_files", "managed_content", "extra_content", "expected_reason"),
    [
        ("\x00", None, None, "format"),
        ("{other} (ignore_errors=no)", None, None, "managed_reference"),
        ("{managed} (ignore_errors=no)", b"incomplete\n", None, "managed_snapshot"),
        (
            "{other} (ignore_errors=no)\n{managed} (ignore_errors=no)",
            None,
            b"CODEV_PLATFORM_CONFIG=/untrusted/overlay.json\n",
            "auxiliary",
        ),
    ],
)
def test_环境文件证明失败只保留固定无秘密原因码(
    tmp_path: Path,
    environment_files: str,
    managed_content: bytes | None,
    extra_content: bytes | None,
    expected_reason: str,
) -> None:
    from codev_platform.ops.reindex_codegraph_resume_config_proof import (
        CodegraphResumeConfigurationError,
    )

    managed = tmp_path / "resume.env"
    other = tmp_path / "other.env"
    with pytest.raises(CodegraphResumeConfigurationError) as raised:
        _verify(
            tmp_path,
            environment_files=environment_files.format(
                managed=managed.as_posix(),
                other=other.as_posix(),
            ),
            managed_content=managed_content,
            extra_files={} if extra_content is None else {other: extra_content},
        )

    assert raised.value.environment_files_reason == expected_reason
    assert "untrusted" not in str(raised.value)


@pytest.mark.parametrize(
    "environment",
    [
        "CODEV_PLATFORM_CONFIG=/srv/overlay.json",
        "CODEV_REINDEX_REPO_OVERRIDE=不可信覆盖",
        "CODEV_PLATFORM_CONFIG=/one CODEV_PLATFORM_CONFIG=/two",
        "PATH=/usr/bin\x00",
    ],
)
def test_恢复配置同源证明拒绝静态环境中的受保护或仓覆盖变量(
    tmp_path: Path,
    environment: str,
) -> None:
    from codev_platform.ops.reindex_codegraph_resume_config_proof import (
        CodegraphResumeConfigurationError,
    )

    with pytest.raises(CodegraphResumeConfigurationError) as raised:
        _verify(tmp_path, environment=environment)

    assert "敏感 systemd 输出" not in str(raised.value)


@pytest.mark.parametrize(
    "managed_content",
    [
        b"CODEV_PLATFORM_CONFIG=/one\nPLATFORM_DATA_DIR=/data\n",
        (
            b"CODEV_PLATFORM_CONFIG=/one\n"
            b"CODEV_PLATFORM_CONFIG=/two\n"
            b"PLATFORM_DATA_DIR=/data\n" + f"CODEV_REINDEX_CONFIG_SHA256={_DIGEST}\n".encode()
        ),
        (b"CODEV_PLATFORM_CONFIG=/one\nPLATFORM_DATA_DIR=/data\nCODEV_REINDEX_CONFIG_SHA256=bad\n"),
        "CODEV_REINDEX_REPO_OVERRIDE=不可信覆盖\n".encode(),
    ],
)
def test_恢复配置同源证明拒绝缺失重复或不可信的受管快照文件(
    tmp_path: Path,
    managed_content: bytes,
) -> None:
    from codev_platform.ops.reindex_codegraph_resume_config_proof import (
        CodegraphResumeConfigurationError,
    )

    with pytest.raises(CodegraphResumeConfigurationError):
        _verify(tmp_path, managed_content=managed_content)


def test_恢复配置同源证明拒绝快照文件与调用方不一致(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume_config_proof import (
        CodegraphResumeConfigurationError,
    )

    with pytest.raises(CodegraphResumeConfigurationError):
        _verify(
            tmp_path,
            managed_content=(
                b"CODEV_PLATFORM_CONFIG=/other/overlay.json\n"
                b"PLATFORM_DATA_DIR=/other/data\n"
                + f"CODEV_REINDEX_CONFIG_SHA256={_DIGEST}\n".encode()
            ),
        )


@pytest.mark.parametrize(
    "environment_files",
    [
        "{managed} (ignore_errors=yes)",
        "{managed} (ignore_errors=no) {managed} (ignore_errors=no)",
        "{other} (ignore_errors=no)",
    ],
)
def test_恢复配置同源证明要求每个unit精确引用受管快照文件(
    tmp_path: Path,
    environment_files: str,
) -> None:
    from codev_platform.ops.reindex_codegraph_resume_config_proof import (
        CodegraphResumeConfigurationError,
    )

    managed = tmp_path / "resume.env"
    other = tmp_path / "other.env"
    with pytest.raises(CodegraphResumeConfigurationError):
        _verify(
            tmp_path,
            environment_files=environment_files.format(
                managed=managed.as_posix(),
                other=other.as_posix(),
            ),
        )


def test_恢复配置同源证明失败保留固定unit与属性位置(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume_config_proof import (
        CodegraphResumeConfigurationError,
    )

    with pytest.raises(CodegraphResumeConfigurationError) as raised:
        _verify(tmp_path, environment_files="/tmp/other.env (ignore_errors=no)")

    assert raised.value.unit == "codev-reindex.service"
    assert raised.value.property_name == "EnvironmentFiles"
    assert raised.value.dropin_active is False


@pytest.mark.parametrize(
    ("dropin_paths", "expected"),
    [
        (
            "/run/systemd/system/codev-reindex.service.d/20-codev-reindex-codegraph-resume.conf",
            False,
        ),
        (
            "/etc/systemd/system/codev-reindex.service.d/20-codev-reindex-codegraph-resume.conf",
            True,
        ),
    ],
)
def test_恢复配置活性只接受固定受管dropin完整路径(
    tmp_path: Path,
    dropin_paths: str,
    expected: bool,
) -> None:
    from codev_platform.ops.reindex_codegraph_resume_config_proof import (
        CodegraphResumeConfigurationError,
    )

    with pytest.raises(CodegraphResumeConfigurationError) as raised:
        _verify(
            tmp_path,
            environment_files="/tmp/other.env (ignore_errors=no)",
            dropin_paths=dropin_paths,
        )

    assert raised.value.dropin_active is expected


def test_恢复配置同源证明拒绝指向受管文件的相对路径别名(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.ops.reindex_codegraph_resume_config_proof import (
        CodegraphResumeConfigurationError,
    )

    monkeypatch.chdir(tmp_path)
    with pytest.raises(CodegraphResumeConfigurationError):
        _verify(tmp_path, environment_files="resume.env (ignore_errors=no)")


def test_恢复配置同源证明在可信读取前不解析环境文件路径(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform.ops import reindex_codegraph_resume_config_proof as module

    configured = tmp_path / "environment" / "token.env"

    def resolve_path(*_args: object, **_kwargs: object) -> Path:
        pytest.fail("环境文件必须交给可信读取叶子证明")

    monkeypatch.setattr(module.Path, "resolve", resolve_path)

    files = module._parse_environment_files(f"{configured.as_posix()} (ignore_errors=no)")

    assert files == (configured,)


def test_恢复配置同源证明不解析受管快照路径(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform.ops import reindex_codegraph_resume_config_proof as module

    configured = tmp_path / "resume.env"

    def resolve_path(*_args: object, **_kwargs: object) -> Path:
        pytest.fail("受管快照路径不得在同源比较前解析")

    monkeypatch.setattr(module.Path, "resolve", resolve_path)

    assert module._normalize_managed_environment_path(configured) == configured


def test_恢复配置同源证明拒绝含父级跳转的环境文件路径(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume_config_proof import (
        CodegraphResumeConfigurationError,
    )

    alias = f"{tmp_path.as_posix()}/temporary/../resume.env"

    with pytest.raises(CodegraphResumeConfigurationError):
        _verify(tmp_path, environment_files=f"{alias} (ignore_errors=no)")


def test_恢复配置同源证明拒绝其他环境文件覆盖受保护变量(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_resume_config_proof import (
        CodegraphResumeConfigurationError,
    )

    managed = tmp_path / "resume.env"
    token_file = tmp_path / "token.env"
    with pytest.raises(CodegraphResumeConfigurationError):
        _verify(
            tmp_path,
            environment_files=(
                f"{token_file.as_posix()} (ignore_errors=no) "
                f"{managed.as_posix()} (ignore_errors=no)"
            ),
            extra_files={token_file: "CODEV_REINDEX_REPO_OVERRIDE=不可信覆盖\n".encode()},
        )


@pytest.mark.parametrize(
    ("property_name", "value"),
    [
        ("--property=UnsetEnvironment", "CODEV_PLATFORM_CONFIG"),
        ("--property=PassEnvironment", "PLATFORM_DATA_DIR"),
        ("--property=PassEnvironment", "CODEV_REINDEX_REPO_OVERRIDE"),
        ("--property=PAMName", "login"),
    ],
)
def test_恢复配置同源证明拒绝其他systemd环境来源(
    tmp_path: Path,
    property_name: str,
    value: str,
) -> None:
    from codev_platform.ops.reindex_codegraph_resume_config_proof import (
        CodegraphResumeConfigurationError,
    )

    with pytest.raises(CodegraphResumeConfigurationError):
        _verify(tmp_path, overrides={property_name: value})
