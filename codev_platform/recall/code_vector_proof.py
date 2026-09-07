"""代码向量集合的独立进程完整性证明。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import subprocess
import sys
import time
import uuid

from codev_platform.chroma.collection_integrity import (
    CollectionIntegrityError,
    CollectionProbeUnavailableError,
)
from codev_platform.recall.code_vector_io import write_json_atomic


logger = logging.getLogger(__name__)
_PROBE_ATTEMPTS = 4
_PROBE_BACKOFF_SECONDS = 2.0
_PROBE_PROCESS_TIMEOUT_SECONDS = 30


@dataclass(frozen=True, slots=True)
class _CollectionProofResult:
    """一次独立探针返回的集合规模与进程号。"""

    count: int
    pid: int


def _probe_collection_ids_in_subprocess(
    directory: Path,
    collection_name: str,
    manifest: dict,
    *,
    atomic_writer: Callable[[Path, object], None] = write_json_atomic,
) -> _CollectionProofResult:
    """在一次性短进程中打开 Chroma 并核对全部 ID。"""
    expected_path = directory / f".code_vec_proof.{uuid.uuid4().hex}.json"
    atomic_writer(expected_path, manifest)
    probe_script = Path(__file__).with_name("code_vector_probe.py")
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                str(probe_script),
                "--directory",
                str(directory),
                "--collection",
                collection_name,
                "--manifest",
                str(expected_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PROBE_PROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CollectionProbeUnavailableError("code_vec 独立完整性探针不可用") from exc
    finally:
        expected_path.unlink(missing_ok=True)

    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, TypeError, ValueError) as exc:
        raise CollectionProbeUnavailableError("code_vec 独立完整性探针协议无效") from exc
    if completed.returncode == 0 and payload.get("status") == "ok":
        count = payload.get("count")
        pid = payload.get("pid")
        if type(count) is int and count >= 0 and type(pid) is int and pid > 0:
            return _CollectionProofResult(count=count, pid=pid)
        raise CollectionProbeUnavailableError("code_vec 独立完整性探针结果无效")
    if completed.returncode == 65 and payload.get("status") == "integrity_error":
        raise CollectionIntegrityError("code_vec 完整性失败")
    error_type = payload.get("error_type")
    detail = error_type if isinstance(error_type, str) and error_type else "unknown"
    raise CollectionProbeUnavailableError(f"code_vec 独立完整性探针失败({detail})")


def _run_collection_proof_with_retry(
    probe: Callable[[], int],
    manifest_path: Path,
    *,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    """有限重试暂态探针；结构错误立即撤销 manifest。"""
    for attempt in range(1, _PROBE_ATTEMPTS + 1):
        try:
            return probe()
        except CollectionProbeUnavailableError:
            if attempt == _PROBE_ATTEMPTS:
                manifest_path.unlink(missing_ok=True)
                raise
            delay = _PROBE_BACKOFF_SECONDS * attempt
            logger.warning(
                "[code_vec] 完整性探针暂不可用，第 %s/%s 次，%.1f 秒后重试",
                attempt,
                _PROBE_ATTEMPTS,
                delay,
            )
            sleeper(delay)
        except CollectionIntegrityError:
            manifest_path.unlink(missing_ok=True)
            raise
    raise AssertionError("code_vec 完整性探针重试状态不可达")


def _verify_collection_ids(
    target,
    manifest: dict,
    manifest_path: Path,
    *,
    subprocess_probe: Callable[
        [Path, str, dict], _CollectionProofResult
    ] = _probe_collection_ids_in_subprocess,
    proof_runner: Callable[[Callable[[], int], Path], int] = _run_collection_proof_with_retry,
) -> int:
    """验证构建目标的 collection 名称与全部向量 ID。"""
    collection_name = getattr(target.collection, "name", None)
    if not isinstance(collection_name, str) or not collection_name:
        manifest_path.unlink(missing_ok=True)
        raise CollectionIntegrityError("code_vec collection 名称无效")

    def probe() -> int:
        return subprocess_probe(target.build_dir, collection_name, manifest).count

    return proof_runner(probe, manifest_path)
