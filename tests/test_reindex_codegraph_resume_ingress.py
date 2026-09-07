"""CodeGraph 最终交接到 Webhook 入口恢复的受控适配器回归。"""

from __future__ import annotations

from pathlib import Path

import pytest


def _context(tmp_path: Path):
    from codev_platform.core.runtime_interpreter import ReleaseInterpreterIdentity
    from codev_platform.ops.reindex_codegraph_resume_context import CodegraphResumeContext

    config_path = tmp_path / "overlay.json"
    config_path.write_text("{}", encoding="utf-8")
    repository = tmp_path / "repo"
    repository.mkdir()
    return CodegraphResumeContext(
        project_id="demo",
        target_commit="a" * 40,
        runtime_release=ReleaseInterpreterIdentity(
            runtime_revision="b" * 40,
            release_id="c" * 64,
            interpreter_path="/release/venv/bin/python",
        ),
        config_path=config_path.resolve(),
        config_digest="d" * 64,
        data_root=(tmp_path / "data").resolve(),
        manifest_path=(tmp_path / "data" / "index_manifest.sqlite").resolve(),
        repositories=(repository.resolve(),),
        health_url="http://127.0.0.1:18091/healthz",
    )


def test_入口恢复复用同一配置快照并把健康证明交给生命周期(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform.core import config as config_module
    from codev_platform.ops import reindex_codegraph_resume_adapters as adapters
    from codev_platform.ops import reindex_webhook_maintenance as webhook_maintenance
    from codev_platform import runtime_webhook_acceptance

    context = _context(tmp_path)
    events: list[object] = []
    configuration = {"webhook": {"port": 18080}}
    monkeypatch.setenv("CODEV_PLATFORM_CONFIG", context.config_path.as_posix())
    monkeypatch.setattr(config_module, "load_config", lambda: configuration)
    monkeypatch.setattr(config_module, "config_snapshot_digest", lambda _cfg: context.config_digest)
    monkeypatch.setattr(
        runtime_webhook_acceptance,
        "verify_webhook_http",
        lambda cfg: events.append(("health", cfg)),
    )

    def resume(*, health_proof, **_kwargs) -> None:
        events.append("resume")
        health_proof()

    monkeypatch.setattr(webhook_maintenance, "resume_webhook_maintenance", resume)

    adapters.default_resume_ingress(context)

    assert events == ["resume", ("health", configuration)]


def test_入口恢复在配置覆盖或摘要漂移时不解除hold(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform.core import config as config_module
    from codev_platform.ops import reindex_codegraph_resume_adapters as adapters
    from codev_platform.ops import reindex_webhook_maintenance as webhook_maintenance

    context = _context(tmp_path)
    monkeypatch.setenv("CODEV_PLATFORM_CONFIG", context.config_path.as_posix())
    monkeypatch.setattr(config_module, "load_config", lambda: {})
    monkeypatch.setattr(config_module, "config_snapshot_digest", lambda _cfg: "e" * 64)
    monkeypatch.setattr(
        webhook_maintenance,
        "resume_webhook_maintenance",
        lambda **_kwargs: pytest.fail("配置漂移前不得尝试开放入口"),
    )

    with pytest.raises(RuntimeError, match="Webhook 恢复配置无法证明"):
        adapters.default_resume_ingress(context)
