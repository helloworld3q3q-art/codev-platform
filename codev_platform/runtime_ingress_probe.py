"""Webhook 最终开放后的签名事件与入队闭环探针。"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path


def run_probe(
    *,
    config_path: Path,
    project_id: str,
    target_commit: str,
    runtime_revision: str,
    timeout_sec: float,
) -> str:
    from codev_platform.core.config import get, load_config
    from codev_platform.core.paths import index_manifest_path
    from codev_platform.ops.reindex_ingress_proof import (
        capture_ingress_manifest_baseline,
        wait_for_ingress_manifests,
    )
    from codev_platform.reindex import open_default_queue
    from codev_platform.runtime_asgi_probe import post_asgi_request
    from codev_platform.runtime_managed_configuration import MANAGED_CONFIG_PATH
    from codev_platform.webhook.server import build_app

    if config_path != MANAGED_CONFIG_PATH:
        raise RuntimeError("Webhook 探针配置路径无效")
    os.environ["CODEV_PLATFORM_CONFIG"] = config_path.as_posix()
    cfg = load_config()
    project = get(cfg, f"projects.{project_id}")
    repository = project.get("webhook_repo") if isinstance(project, dict) else None
    secret = get(cfg, "webhook.secret")
    if type(repository) is not str or not repository.strip():
        raise RuntimeError("Webhook 项目映射未配置")
    if type(secret) is not str or not secret:
        raise RuntimeError("Webhook 签名密钥未配置")
    manifest_path = index_manifest_path()
    baseline = capture_ingress_manifest_baseline(project_id, path=manifest_path)
    body = json.dumps(
        {
            "after": target_commit,
            "commits": [{"added": [], "modified": ["README.md"], "removed": []}],
            "repository": {"full_name": repository.strip()},
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    # 在 target release 进程内执行真实 ASGI/HMAC 路由；外部监听仍被入口门禁关闭。
    response = post_asgi_request(
        build_app(),
        path="/gitea",
        body=body,
        headers=(
            ("Content-Type", "application/json"),
            ("X-Gitea-Event", "push"),
            ("X-Gitea-Signature", signature),
        ),
        max_response_bytes=64 * 1024,
    )
    if response.status_code != 200:
        raise RuntimeError("Webhook 签名探针响应失败")
    try:
        payload = json.loads(response.body)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise RuntimeError("Webhook 签名探针响应无效") from None
    if (
        type(payload) is not dict
        or payload.get("ok") is not True
        or payload.get("project_id") != project_id
        or type(payload.get("enqueued")) is not list
        or not payload["enqueued"]
    ):
        raise RuntimeError("Webhook 签名事件没有形成入队事实")
    affected = tuple(payload["enqueued"])
    queue = open_default_queue(fail_soft=False)
    proof = wait_for_ingress_manifests(
        project_id,
        target_commit,
        runtime_revision,
        affected,
        baseline,
        path=manifest_path,
        queue_snapshot=queue.snapshot,
        timeout_sec=timeout_sec,
    )
    return hashlib.sha256(
        (
            f"{proof.evidence_sha256}\n{project_id}\n{target_commit}\n"
            + "\n".join(proof.kinds)
            + "\n"
        ).encode("ascii")
    ).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Webhook 签名闭环探针")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--target-commit", required=True)
    parser.add_argument("--runtime-revision", required=True)
    parser.add_argument("--timeout-sec", type=float, required=True)
    args = parser.parse_args(argv)
    try:
        evidence = run_probe(
            config_path=args.config,
            project_id=args.project,
            target_commit=args.target_commit,
            runtime_revision=args.runtime_revision,
            timeout_sec=args.timeout_sec,
        )
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        print('{"error":"webhook_probe_failed","status":"error"}', flush=True)
        return 1
    print(
        json.dumps(
            {"evidence_sha256": evidence, "status": "ok"},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "run_probe"]
