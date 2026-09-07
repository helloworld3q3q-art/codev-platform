"""Webhook 最终验收探针的进程内签名事件测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import urllib.request

from codev_platform.ops.reindex_ingress_proof import IngressManifestBaseline
from codev_platform.reindex.queue_ports import QueueSnapshot
from codev_platform.runtime_ingress_probe import run_probe


_PROJECT = "codev-platform"
_TARGET = "a" * 40


class _Queue:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def enqueue(self, project_id, kind, *, meta) -> None:
        assert project_id == _PROJECT
        assert kind == "chroma"
        assert meta.source == "webhook"
        assert meta.target_commit == _TARGET
        self.events.append("enqueue")

    @staticmethod
    def snapshot() -> QueueSnapshot:
        return QueueSnapshot()


def test_服务未开放时用target进程内应用发送签名事件并等待新attempt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import codev_platform.core.config as config
    import codev_platform.core.paths as paths
    import codev_platform.core.runtime_identity as runtime_identity_module
    import codev_platform.ops.reindex_ingress_proof as proof_module
    import codev_platform.reindex as reindex
    import codev_platform.runtime_managed_configuration as managed_configuration
    import codev_platform.webhook.server as webhook

    manifest = tmp_path / "manifest.sqlite"
    manifest.touch()
    managed_config = tmp_path / "config.json"
    cfg = {
        "webhook": {"secret": "test-secret"},
        "projects": {_PROJECT: {"webhook_repo": "owner/repository"}},
    }
    events: list[str] = []
    baseline = IngressManifestBaseline(
        project_id=_PROJECT,
        attempts=(
            ("chroma", "old-chroma"),
            ("codegraph", "old-codegraph"),
            ("ingest", "old-ingest"),
            ("code_vec", "old-code-vec"),
        ),
    )
    monkeypatch.setattr(managed_configuration, "MANAGED_CONFIG_PATH", managed_config)
    monkeypatch.setattr(config, "load_config", lambda: cfg)
    monkeypatch.setattr(webhook, "load_config", lambda: cfg)
    monkeypatch.setattr(webhook, "_target_projects_for", lambda _cfg, pid: [pid])
    monkeypatch.setattr(webhook, "_scopes_for", lambda _pid, _changed: ["chroma"])
    monkeypatch.setattr(paths, "index_manifest_path", lambda: manifest)
    monkeypatch.setattr(
        runtime_identity_module,
        "runtime_identity",
        lambda: SimpleNamespace(as_dict=lambda: {}),
    )
    monkeypatch.setattr(reindex, "open_default_queue", lambda **_kwargs: _Queue(events))
    monkeypatch.setattr(
        proof_module,
        "capture_ingress_manifest_baseline",
        lambda _project, *, path: events.append("baseline") or baseline,
    )

    def wait(
        _project,
        _target,
        _runtime,
        kinds,
        received_baseline,
        **_kwargs,
    ):
        assert kinds == ("chroma",)
        assert received_baseline is baseline
        events.append("wait")
        return SimpleNamespace(evidence_sha256="e" * 64, kinds=("chroma",))

    monkeypatch.setattr(proof_module, "wait_for_ingress_manifests", wait)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不得开放网络端口")),
    )

    evidence = run_probe(
        config_path=managed_config,
        project_id=_PROJECT,
        target_commit=_TARGET,
        runtime_revision=_TARGET,
        timeout_sec=10.0,
    )

    assert evidence != "e" * 64
    assert len(evidence) == 64
    assert events == ["baseline", "enqueue", "wait"]
