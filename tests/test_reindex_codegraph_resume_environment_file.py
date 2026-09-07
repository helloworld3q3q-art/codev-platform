"""受控恢复环境文件的可信读取适配测试。"""
from __future__ import annotations

from pathlib import Path

import pytest


def test_环境文件读取委派root可信dirfd叶子并保留字节上限(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform.ops import reindex_codegraph_resume_environment_file as module

    target = (tmp_path / "resume.env").resolve()
    calls: list[tuple[Path, int]] = []
    monkeypatch.setattr(
        module,
        "read_optional_root_owned_regular_file",
        lambda path, *, max_bytes: calls.append((path, max_bytes)) or b"SAFE=value\n",
    )

    assert module.read_trusted_environment_file(target) == b"SAFE=value\n"
    assert calls == [(target, module._MAX_ENVIRONMENT_FILE_BYTES)]


def test_环境文件缺失或受管路径异常一律映射为不受信任(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform.ops import reindex_codegraph_resume_environment_file as module
    from codev_platform.ops import reindex_codegraph_resume_managed_path as managed

    target = (tmp_path / "resume.env").resolve()
    monkeypatch.setattr(module, "read_optional_root_owned_regular_file", lambda *_args, **_kwargs: None)

    with pytest.raises(module.TrustedEnvironmentFileError):
        module.read_trusted_environment_file(target)

    monkeypatch.setattr(
        module,
        "read_optional_root_owned_regular_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            managed.TrustedManagedPathError("unsafe")
        ),
    )
    with pytest.raises(module.TrustedEnvironmentFileError):
        module.read_trusted_environment_file(target)


def test_环境文件拒绝非绝对或含父目录回退的路径(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.ops import reindex_codegraph_resume_environment_file as module

    monkeypatch.setattr(
        module,
        "read_optional_root_owned_regular_file",
        lambda *_args, **_kwargs: pytest.fail("非法路径不得委派"),
    )

    with pytest.raises(module.TrustedEnvironmentFileError):
        module.read_trusted_environment_file(Path("relative.env"))
    with pytest.raises(module.TrustedEnvironmentFileError):
        module.read_trusted_environment_file(Path("/etc/codev/../resume.env"))
