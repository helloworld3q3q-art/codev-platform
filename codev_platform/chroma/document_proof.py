"""文档 Chroma collection 的独立进程完整性证明。"""
from __future__ import annotations

from collections.abc import Callable
import json
import logging
from pathlib import Path
import subprocess
import sys
import time

from codev_platform.chroma.collection_integrity import (
    CollectionIntegrityError,
    CollectionProbeUnavailableError,
)


logger = logging.getLogger(__name__)
_PROBE_ATTEMPTS = 4
_PROBE_BACKOFF_SECONDS = 2.0
_PROBE_PROCESS_TIMEOUT_SECONDS = 30


def _invoke_document_probe(
    directory: Path,
    collection_name: str,
    manifest_path: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> int:
    """在一次性短进程中打开持久化 collection 并证明完整 ID 集。"""
    try:
        completed = runner(
            [
                sys.executable,
                "-I",
                "-m",
                "codev_platform.chroma.document_probe",
                "--directory",
                str(directory),
                "--collection",
                collection_name,
                "--manifest",
                str(manifest_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PROBE_PROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CollectionProbeUnavailableError("Chroma 独立完整性探针不可用") from exc

    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, TypeError, ValueError) as exc:
        raise CollectionProbeUnavailableError("Chroma 独立完整性探针协议无效") from exc
    if completed.returncode == 0 and payload.get("status") == "ok":
        count = payload.get("count")
        pid = payload.get("pid")
        if type(count) is int and count >= 0 and type(pid) is int and pid > 0:
            return count
        raise CollectionProbeUnavailableError("Chroma 独立完整性探针结果无效")
    if completed.returncode == 65 and payload.get("status") == "integrity_error":
        raise CollectionIntegrityError("Chroma 完整性失败")
    error_type = payload.get("error_type")
    detail = error_type if isinstance(error_type, str) and error_type else "unknown"
    raise CollectionProbeUnavailableError(f"Chroma 独立完整性探针失败({detail})")


def verify_document_collection_in_subprocess(
    directory: Path,
    collection_name: str,
    manifest_path: Path,
    *,
    probe: Callable[[Path, str, Path], int] = _invoke_document_probe,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    """有界重试独立持久化证明；结构错误或耗尽时撤销 manifest。"""
    for attempt in range(1, _PROBE_ATTEMPTS + 1):
        try:
            return probe(directory, collection_name, manifest_path)
        except CollectionProbeUnavailableError:
            if attempt == _PROBE_ATTEMPTS:
                manifest_path.unlink(missing_ok=True)
                raise
            delay = _PROBE_BACKOFF_SECONDS * attempt
            logger.warning(
                "Chroma 独立完整性探针暂不可用，第 %s/%s 次，%.1f 秒后重试",
                attempt,
                _PROBE_ATTEMPTS,
                delay,
            )
            sleeper(delay)
        except CollectionIntegrityError:
            manifest_path.unlink(missing_ok=True)
            raise
    raise AssertionError("Chroma 独立完整性探针重试状态不可达")


__all__ = ["verify_document_collection_in_subprocess"]
