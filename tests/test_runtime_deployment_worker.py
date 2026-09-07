"""target release 部署叶子的安全信封、资源释放与观测权限迁移测试。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import codev_platform.runtime_deployment_worker as worker
from codev_platform.runtime_deployment_contract import RuntimeDeploymentError


_CONFIG = Path("/etc/codev-platform/config.json")


class _Engine:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


def test_worker输出解析器只接受唯一成功信封() -> None:
    assert worker.parse_worker_output('{"result":{"count":1},"status":"ok"}') == {"count": 1}

    for invalid in (
        '{"result":{},"status":"error"}',
        '{"result":{},"status":"ok","token":"secret"}',
        '{"result":[],"status":"ok"}',
        "not-json",
    ):
        with pytest.raises(RuntimeDeploymentError):
            worker.parse_worker_output(invalid)


def test_数据库只读验收返回head与摘要并始终释放engine(monkeypatch) -> None:
    from codev_platform.web.db import engine as engine_module
    from codev_platform.web.db import migration_postgres

    engine = _Engine()
    monkeypatch.setattr(worker, "_configure", lambda _path: {"memory": {"pg_dsn": "hidden"}})
    monkeypatch.setattr(
        "codev_platform.core.config.env_or_config",
        lambda *_args: "postgresql://user:secret@host/db",
    )
    monkeypatch.setattr(engine_module, "make_engine", lambda _dsn: engine)
    monkeypatch.setattr(
        migration_postgres,
        "verify_postgres_current",
        lambda _engine: SimpleNamespace(
            head_revision="0009_agent_memory",
            schema_sha256="a" * 64,
            table_count=12,
        ),
    )

    result = worker._verify_database(_CONFIG)

    assert result["head_revision"] == "0009_agent_memory"
    assert result["schema_sha256"] == "a" * 64
    assert len(result["evidence_sha256"]) == 64
    assert "secret" not in json.dumps(result)
    assert engine.disposed is True


def test_cuda验收叶子只返回能力计数和摘要(monkeypatch) -> None:
    import codev_platform.runtime_cuda_acceptance as cuda

    monkeypatch.setattr(worker, "_configure", lambda _path: {})
    monkeypatch.setattr(
        cuda,
        "verify_cuda_runtime",
        lambda **_kwargs: SimpleNamespace(
            available=True,
            device_count=1,
            evidence_sha256="b" * 64,
        ),
    )

    assert worker._verify_cuda(_CONFIG, True) == {
        "available": True,
        "device_count": 1,
        "evidence_sha256": "b" * 64,
    }


def test_mcp验收叶子加载受管配置并只返回聚合摘要(monkeypatch) -> None:
    import codev_platform.runtime_mcp_acceptance as mcp_acceptance

    config = {"gateway": {"auth_mode": "token"}}
    captured: list[object] = []
    monkeypatch.setattr(worker, "_configure", lambda _path: config)
    monkeypatch.setattr(
        mcp_acceptance,
        "verify_mcp_acceptance",
        lambda cfg, project_id: (
            captured.extend((cfg, project_id)) or SimpleNamespace(evidence_sha256="c" * 64)
        ),
    )

    assert worker._verify_mcp(_CONFIG, "codev-platform") == {
        "evidence_sha256": "c" * 64,
        "services": ["platform-docs", "codegraph", "graph", "agent-memory"],
    }
    assert captured == [config, "codev-platform"]


def test_webhook验收叶子访问真实监听并只返回聚合摘要(monkeypatch) -> None:
    import codev_platform.runtime_webhook_acceptance as webhook_acceptance

    config = {"webhook": {"port": 8765}}
    monkeypatch.setattr(worker, "_configure", lambda _path: config)
    monkeypatch.setattr(
        webhook_acceptance,
        "verify_webhook_http",
        lambda cfg: (
            SimpleNamespace(evidence_sha256="d" * 64)
            if cfg is config
            else pytest.fail("必须复用受管配置快照")
        ),
    )

    assert worker._verify_webhook(_CONFIG) == {
        "evidence_sha256": "d" * 64,
        "service": "webhook",
    }


def test_mcp验收子命令接线到固定叶子(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        worker,
        "_verify_mcp",
        lambda config, project: {
            "config": config.as_posix(),
            "project": project,
        },
    )

    code = worker.main(
        [
            "--config",
            _CONFIG.as_posix(),
            "verify-mcp",
            "--project",
            "codev-platform",
        ]
    )

    assert code == 0
    assert json.loads(capsys.readouterr().out)["result"] == {
        "config": _CONFIG.as_posix(),
        "project": "codev-platform",
    }


def test_叶子失败只输出固定错误码不回显底层异常(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        worker,
        "_verify_cuda",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("token=top-secret")),
    )

    code = worker.main(
        [
            "--config",
            _CONFIG.as_posix(),
            "verify-cuda",
            "--require-cuda",
        ]
    )

    captured = capsys.readouterr()
    assert code == 1
    assert "top-secret" not in captured.err
    assert json.loads(captured.err) == {
        "error": "deployment_verify-cuda_failed",
        "status": "error",
    }


def test_观测权限迁移使用受管配置同源数据根和真实服务身份(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import codev_platform.runtime_artifact_access as artifact_access
    import codev_platform.runtime_deployment_worker as worker
    import codev_platform.runtime_service_process as service_process
    from codev_platform.core import paths

    root = (tmp_path / "data").resolve()
    captured: list[object] = []
    monkeypatch.setattr(worker, "_configure", lambda _path: {"managed": True})
    monkeypatch.setattr(
        paths,
        "privileged_data_root",
        lambda *, cfg: captured.append(cfg) or root,
    )
    monkeypatch.setattr(
        paths,
        "data_root",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("禁止提前解析数据根")),
    )
    monkeypatch.setattr(
        service_process,
        "resolve_service_account",
        lambda user: captured.append(user) or SimpleNamespace(uid=1000, gid=1001),
    )
    monkeypatch.setattr(
        artifact_access,
        "migrate_runtime_artifact_access",
        lambda layout, *, service_uid, service_gid: (
            captured.append((layout.root, service_uid, service_gid))
            or artifact_access.RuntimeArtifactAccessProof(
                schema_version=1,
                namespace_count=5,
                entry_count=8,
                changed_entry_count=3,
                target_uid=1000,
                target_gid=1001,
                evidence_sha256="7" * 64,
            )
        ),
    )

    result = worker._converge_runtime_artifacts(Path("/etc/codev-platform/config.json"), "svc")

    assert captured == [
        "svc",
        {"managed": True},
        (root, 1000, 1001),
    ]
    assert result == {
        "changed_entry_count": 3,
        "entry_count": 8,
        "evidence_sha256": "7" * 64,
        "namespace_count": 5,
        "schema_version": 1,
        "target_gid": 1001,
        "target_uid": 1000,
    }


def test_部署叶子公开观测权限迁移动作只输出成功信封(monkeypatch, capsys) -> None:
    import codev_platform.runtime_deployment_worker as worker

    expected = {
        "changed_entry_count": 0,
        "entry_count": 5,
        "evidence_sha256": "8" * 64,
        "namespace_count": 5,
        "schema_version": 1,
        "target_gid": 1000,
        "target_uid": 1000,
    }
    monkeypatch.setattr(worker, "_converge_runtime_artifacts", lambda *_args: expected)

    rc = worker.main(
        [
            "--config",
            "/etc/codev-platform/config.json",
            "converge-runtime-artifacts",
            "--service-user",
            "helloworld",
        ]
    )

    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {"result": expected, "status": "ok"}
