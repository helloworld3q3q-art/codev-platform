"""不可变 target release 内执行的无秘密部署叶子动作。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from codev_platform.runtime_deployment_contract import RuntimeDeploymentError


def _configure(path: Path) -> dict:
    from codev_platform.runtime_managed_configuration import MANAGED_CONFIG_PATH

    if path != MANAGED_CONFIG_PATH:
        raise RuntimeError("机器配置路径不是固定受管路径")
    os.environ["CODEV_PLATFORM_CONFIG"] = path.as_posix()
    from codev_platform.core.config import load_config

    return load_config()


def _migrate(config_path: Path) -> dict[str, object]:
    from codev_platform.core.config import env_or_config
    from codev_platform.web.db.engine import make_engine
    from codev_platform.web.db.migration_postgres import migrate_postgres

    cfg = _configure(config_path)
    dsn = env_or_config("CODEV_PLATFORM_MEMORY_DSN", cfg, "memory.pg_dsn")
    if type(dsn) is not str or not dsn.strip():
        raise RuntimeError("数据库连接未配置")
    engine = make_engine(dsn.strip())
    try:
        report = migrate_postgres(engine)
    finally:
        engine.dispose()
    return {
        "adopted_revision": report.adopted_revision,
        "head_revision": report.head_revision,
        "legacy_adopted": report.legacy_adopted,
        "previous_revision": report.previous_revision,
    }


def _converge_runtime_artifacts(
    config_path: Path,
    service_user: str,
) -> dict[str, object]:
    """用受管环境的数据根，把观测产物收敛到真实服务身份。"""
    from codev_platform.core.paths import privileged_data_root
    from codev_platform.core.runtime_artifacts import RuntimeArtifactLayout
    from codev_platform.runtime_artifact_access import (
        RuntimeArtifactAccessProof,
        migrate_runtime_artifact_access,
    )
    from codev_platform.runtime_service_process import resolve_service_account

    cfg = _configure(config_path)
    account = resolve_service_account(service_user)
    proof = migrate_runtime_artifact_access(
        RuntimeArtifactLayout(privileged_data_root(cfg=cfg)),
        service_uid=account.uid,
        service_gid=account.gid,
    )
    if type(proof) is not RuntimeArtifactAccessProof:
        raise RuntimeError("运行观测产物权限迁移回执无效")
    return {
        "changed_entry_count": proof.changed_entry_count,
        "entry_count": proof.entry_count,
        "evidence_sha256": proof.evidence_sha256,
        "namespace_count": proof.namespace_count,
        "schema_version": proof.schema_version,
        "target_gid": proof.target_gid,
        "target_uid": proof.target_uid,
    }


def _wait_indexes(
    config_path: Path,
    project_id: str,
    target_commit: str,
    runtime_revision: str,
    timeout_sec: float,
) -> dict[str, object]:
    from codev_platform.core.paths import index_manifest_path
    from codev_platform.ops.reindex_manifest_proof import wait_for_all_reindex_manifests
    from codev_platform.reindex import open_default_queue

    _configure(config_path)
    queue = open_default_queue(fail_soft=False)
    proof = wait_for_all_reindex_manifests(
        project_id,
        target_commit,
        runtime_revision,
        path=index_manifest_path(),
        queue_snapshot=queue.snapshot,
        timeout_sec=timeout_sec,
    )
    return {
        "evidence_sha256": proof.evidence_sha256,
        "kinds": list(proof.kinds),
        "runtime_revision": proof.runtime_revision,
        "target_commit": proof.target_commit,
    }


def _verify_database(config_path: Path) -> dict[str, object]:
    """用只读事务独立复验当前 head、受管表和运行契约。"""
    from codev_platform.core.config import env_or_config
    from codev_platform.web.db.engine import make_engine
    from codev_platform.web.db.migration_postgres import verify_postgres_current

    cfg = _configure(config_path)
    dsn = env_or_config("CODEV_PLATFORM_MEMORY_DSN", cfg, "memory.pg_dsn")
    if type(dsn) is not str or not dsn.strip():
        raise RuntimeError("数据库连接未配置")
    engine = make_engine(dsn.strip())
    try:
        report = verify_postgres_current(engine)
    finally:
        engine.dispose()
    evidence = hashlib.sha256(
        f"{report.head_revision}\n{report.schema_sha256}\n{report.table_count}\n".encode("ascii")
    ).hexdigest()
    return {
        "evidence_sha256": evidence,
        "head_revision": report.head_revision,
        "schema_sha256": report.schema_sha256,
        "table_count": report.table_count,
    }


def _verify_cuda(config_path: Path, require_cuda: bool) -> dict[str, object]:
    """在 target release 内执行 CUDA 发现与真实张量计算。"""
    from codev_platform.runtime_cuda_acceptance import verify_cuda_runtime

    _configure(config_path)
    report = verify_cuda_runtime(require_cuda=require_cuda)
    return {
        "available": report.available,
        "device_count": report.device_count,
        "evidence_sha256": report.evidence_sha256,
    }


def _verify_mcp(config_path: Path, project_id: str) -> dict[str, object]:
    """从 target release 对四个本机 MCP 执行真实工具调用。"""
    from codev_platform.runtime_mcp_acceptance import (
        mcp_acceptance_services,
        verify_mcp_acceptance,
    )

    cfg = _configure(config_path)
    evidence = verify_mcp_acceptance(cfg, project_id)
    return {
        "evidence_sha256": evidence.evidence_sha256,
        "services": list(mcp_acceptance_services()),
    }


def _verify_webhook(config_path: Path) -> dict[str, object]:
    """从 target release 访问已经开放的真实 loopback HTTP 路由。"""
    from codev_platform.runtime_webhook_acceptance import verify_webhook_http

    evidence = verify_webhook_http(_configure(config_path))
    return {
        "evidence_sha256": evidence.evidence_sha256,
        "service": "webhook",
    }


def parse_worker_output(output: str) -> dict[str, object]:
    """解析部署叶子的唯一成功信封，不接受附加字段或非对象结果。"""
    try:
        value = json.loads(output)
        result = value["result"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise RuntimeDeploymentError("部署叶子回执无效") from None
    if set(value) != {"result", "status"} or value["status"] != "ok" or type(result) is not dict:
        raise RuntimeDeploymentError("部署叶子回执状态无效")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="不可变 release 部署叶子")
    parser.add_argument("--config", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("migrate-database")
    artifacts = subparsers.add_parser("converge-runtime-artifacts")
    artifacts.add_argument("--service-user", required=True)
    subparsers.add_parser("verify-database")
    cuda = subparsers.add_parser("verify-cuda")
    cuda.add_argument("--require-cuda", action="store_true")
    mcp = subparsers.add_parser("verify-mcp")
    mcp.add_argument("--project", required=True)
    subparsers.add_parser("verify-webhook")
    wait = subparsers.add_parser("wait-indexes")
    wait.add_argument("--project", required=True)
    wait.add_argument("--target-commit", required=True)
    wait.add_argument("--runtime-revision", required=True)
    wait.add_argument("--timeout-sec", type=float, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "converge-runtime-artifacts":
            result = _converge_runtime_artifacts(args.config, args.service_user)
        elif args.action == "migrate-database":
            result = _migrate(args.config)
        elif args.action == "verify-database":
            result = _verify_database(args.config)
        elif args.action == "verify-cuda":
            result = _verify_cuda(args.config, args.require_cuda)
        elif args.action == "verify-mcp":
            result = _verify_mcp(args.config, args.project)
        elif args.action == "verify-webhook":
            result = _verify_webhook(args.config)
        else:
            if not 0 < args.timeout_sec <= 43_200:
                raise RuntimeError("索引等待时限无效")
            result = _wait_indexes(
                args.config,
                args.project,
                args.target_commit,
                args.runtime_revision,
                args.timeout_sec,
            )
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        print(
            json.dumps(
                {"error": f"deployment_{args.action}_failed", "status": "error"},
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
            flush=True,
        )
        return 1
    print(
        json.dumps(
            {"result": result, "status": "ok"},
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "parse_worker_output"]
