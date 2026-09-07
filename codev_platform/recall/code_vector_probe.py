"""code_vec 后验完整性证明的短进程入口。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from codev_platform.chroma.collection_integrity import (
    CollectionIntegrityError,
    CollectionProbeUnavailableError,
    verify_collection_ids,
)
from codev_platform.recall.code_vector_manifest import (
    MAX_MANIFEST_BYTES,
    valid_code_vector_manifest,
)

_EXIT_INTEGRITY = 65
_EXIT_UNAVAILABLE = 75


def _emit(status: str, *, count: int | None = None, error_type: str | None = None) -> None:
    print(json.dumps({
        "status": status,
        "count": count,
        "pid": os.getpid(),
        "error_type": error_type,
    }, ensure_ascii=False))


def _read_expected_ids(path: Path) -> dict:
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise CollectionIntegrityError("code_vec manifest 超出大小限制")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not valid_code_vector_manifest(value):
        raise CollectionIntegrityError("code_vec manifest 结构无效")
    return value


def _probe(directory: Path, collection_name: str, manifest_path: Path) -> int:
    import chromadb

    client = chromadb.PersistentClient(path=str(directory))
    try:
        collection = client.get_collection(collection_name)
        manifest = _read_expected_ids(manifest_path)
        return verify_collection_ids(collection, manifest, label="code_vec")
    finally:
        client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args(argv)
    try:
        count = _probe(Path(args.directory), args.collection, Path(args.manifest))
    except CollectionProbeUnavailableError as exc:
        cause = exc.__cause__
        _emit("unavailable", error_type=type(cause).__name__ if cause else type(exc).__name__)
        return _EXIT_UNAVAILABLE
    except CollectionIntegrityError as exc:
        _emit("integrity_error", error_type=type(exc).__name__)
        return _EXIT_INTEGRITY
    except Exception as exc:  # noqa: BLE001 - 打开/关闭/协议异常统一归暂态探针失败
        _emit("unavailable", error_type=type(exc).__name__)
        return _EXIT_UNAVAILABLE
    _emit("ok", count=count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
