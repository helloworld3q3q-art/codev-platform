"""文档 Chroma 独立持久化证明的定向回归。"""
from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from codev_platform.chroma.collection_integrity import (
    CollectionIntegrityError,
    CollectionProbeUnavailableError,
)
from codev_platform.chroma.document_proof import (
    _invoke_document_probe,
    verify_document_collection_in_subprocess,
)


def _completed(returncode: int, payload: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout=payload, stderr="")


def test_独立文档探针仅接受完整成功回执(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    command_seen: list[str] = []

    def runner(command, **_kwargs):
        command_seen.extend(command)
        return _completed(0, '{"status":"ok","count":7,"pid":123}')

    assert _invoke_document_probe(tmp_path, "docs", manifest_path, runner=runner) == 7
    assert command_seen[1:4] == ["-I", "-m", "codev_platform.chroma.document_probe"]


def test_独立文档探针将结构失败映射为完整性错误(tmp_path: Path) -> None:
    def runner(*_args, **_kwargs):
        return _completed(65, '{"status":"integrity_error","error_type":"CollectionIntegrityError"}')

    with pytest.raises(CollectionIntegrityError, match="Chroma 完整性失败"):
        _invoke_document_probe(tmp_path, "docs", tmp_path / "manifest.json", runner=runner)


def test_独立文档证明仅对暂态不可用有界重试(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    calls = 0
    delays: list[float] = []

    def probe(_directory: Path, _collection: str, _manifest: Path) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise CollectionProbeUnavailableError("暂不可用")
        return 9

    assert verify_document_collection_in_subprocess(
        tmp_path,
        "docs",
        manifest_path,
        probe=probe,
        sleeper=delays.append,
    ) == 9
    assert calls == 2
    assert delays == [2.0]
    assert manifest_path.exists()


def test_独立文档证明遇结构错误撤销_manifest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")

    def probe(_directory: Path, _collection: str, _manifest: Path) -> int:
        raise CollectionIntegrityError("ID 不一致")

    with pytest.raises(CollectionIntegrityError, match="ID 不一致"):
        verify_document_collection_in_subprocess(tmp_path, "docs", manifest_path, probe=probe)
    assert not manifest_path.exists()
