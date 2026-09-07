"""reindex 专用 systemd 维护窗口的原子停机与恢复测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.reindex_maintenance_test_support import (
    _dropin,
    _isolate_legacy_reindex_codegraph_boundary as _configure_legacy_reindex_codegraph_boundary,
)


@pytest.fixture(autouse=True)
def _isolate_legacy_reindex_codegraph_boundary(monkeypatch) -> None:
    _configure_legacy_reindex_codegraph_boundary(monkeypatch)


def test_维护命令演练不写systemd且确认动作委派专用服务(monkeypatch) -> None:
    from codev_platform.ops.reindex_maintenance import cmd_reindex_maintenance

    output: list[str] = []
    assert (
        cmd_reindex_maintenance(
            SimpleNamespace(action="prepare", yes=False),
            out=output.append,
            err=lambda _message: pytest.fail("演练不应报错"),
            prepare_runner=lambda: pytest.fail("演练不得写 systemd"),
        )
        == 0
    )
    assert output == ["演练：prepare 会暂停 codev-reindex；确认后请添加 --yes"]

    calls: list[str] = []
    assert (
        cmd_reindex_maintenance(
            SimpleNamespace(action="restore", yes=True),
            out=output.append,
            err=lambda _message: pytest.fail("确认不应报错"),
            restore_runner=lambda: calls.append("restore"),
        )
        == 0
    )
    assert calls == ["restore"]


def test_status只在完整维护门禁均可证明时报告就绪() -> None:
    from codev_platform.ops.reindex_maintenance import cmd_reindex_maintenance

    output: list[str] = []
    checks: list[str] = []
    assert (
        cmd_reindex_maintenance(
            SimpleNamespace(action="status", yes=False),
            out=output.append,
            err=lambda _message: pytest.fail("完整证明不应报错"),
            status_runner=lambda: checks.append("ready"),
        )
        == 0
    )

    assert checks == ["ready"]
    assert output == ["reindex 维护窗口已就绪：门禁、外部 worker、服务与 cgroup 均已证明安全"]


def test_inspect要求受管dropin维护门禁外部worker与停机证明同时成立(tmp_path) -> None:
    from codev_platform.ops.reindex_maintenance import inspect_reindex_maintenance

    dropin = _dropin(tmp_path)
    dropin.parent.mkdir(parents=True)
    dropin.write_text("[Service]\nRestart=no\n", encoding="utf-8")
    events: list[str] = []

    inspect_reindex_maintenance(
        platform_name="linux",
        dropin_path=dropin,
        gate_active_reader=lambda: True,
        gate_record_reader=lambda: SimpleNamespace(phase="maintenance"),
        external_worker_proof=lambda: events.append("external"),
        stop_proof=lambda: events.append("stopped"),
    )

    assert events == ["external", "stopped"]


def test_inspect拒绝恢复待命态作为管理员写入窗口(tmp_path) -> None:
    """restore_armed/claimed 只能待命，不能在此时迁移或初始化 owner。"""
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        inspect_reindex_maintenance,
    )

    dropin = _dropin(tmp_path)
    dropin.parent.mkdir(parents=True)
    dropin.write_text("[Service]\nRestart=no\n", encoding="utf-8")

    with pytest.raises(ReindexMaintenanceError, match="维护稳态"):
        inspect_reindex_maintenance(
            platform_name="linux",
            dropin_path=dropin,
            gate_active_reader=lambda: True,
            gate_record_reader=lambda: SimpleNamespace(phase="restore_armed"),
            external_worker_proof=lambda: pytest.fail("恢复待命态不得开始写入者扫描"),
            stop_proof=lambda: pytest.fail("恢复待命态不得开始停机证明"),
        )


def test_维护命令已注册为独立顶层入口() -> None:
    from codev_platform.cli import build_parser

    args = build_parser().parse_args(["reindex-maintenance", "prepare", "--yes"])

    assert args.cmd == "reindex-maintenance"
    assert args.action == "prepare"
    assert args.yes is True


def test_provision只预置门禁锁而不触及systemd() -> None:
    from codev_platform.ops.reindex_maintenance import provision_reindex_maintenance

    events: list[str] = []

    provision_reindex_maintenance(
        platform_name="linux",
        gate_provisioner=lambda: events.append("provisioned"),
    )

    assert events == ["provisioned"]


def test_provision命令要求确认且只委派门禁预置() -> None:
    from codev_platform.ops.reindex_maintenance import cmd_reindex_maintenance

    output: list[str] = []
    calls: list[str] = []
    assert (
        cmd_reindex_maintenance(
            SimpleNamespace(action="provision", yes=False),
            out=output.append,
            err=lambda _message: pytest.fail("演练不应报错"),
            provision_runner=lambda: pytest.fail("演练不得预置门禁锁"),
        )
        == 0
    )
    assert output == ["演练：provision 会预置 reindex 维护门禁锁；确认后请添加 --yes"]

    assert (
        cmd_reindex_maintenance(
            SimpleNamespace(action="provision", yes=True),
            out=output.append,
            err=lambda _message: pytest.fail("确认不应报错"),
            provision_runner=lambda: calls.append("provisioned"),
        )
        == 0
    )
    assert calls == ["provisioned"]


def test_维护命令向操作员报告受控安全状态而非只报类型名() -> None:
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        cmd_reindex_maintenance,
    )

    errors: list[str] = []

    assert (
        cmd_reindex_maintenance(
            SimpleNamespace(action="restore", yes=True),
            out=lambda _message: pytest.fail("失败不得报告完成"),
            err=errors.append,
            restore_runner=lambda: (_ for _ in ()).throw(
                ReindexMaintenanceError("reindex 维护窗口恢复失败；安全状态未证明")
            ),
        )
        == 1
    )

    assert errors == ["FATAL: reindex 维护窗口恢复失败；安全状态未证明"]


def test_维护命令未知异常不泄露英文类别或内部内容() -> None:
    from codev_platform.ops.reindex_maintenance import cmd_reindex_maintenance

    status_errors: list[str] = []
    assert (
        cmd_reindex_maintenance(
            SimpleNamespace(action="status", yes=False),
            out=lambda _message: pytest.fail("失败不得报告就绪"),
            err=status_errors.append,
            status_runner=lambda: (_ for _ in ()).throw(RuntimeError("内部路径")),
        )
        == 1
    )

    prepare_errors: list[str] = []
    assert (
        cmd_reindex_maintenance(
            SimpleNamespace(action="prepare", yes=True),
            out=lambda _message: pytest.fail("失败不得报告完成"),
            err=prepare_errors.append,
            prepare_runner=lambda: (_ for _ in ()).throw(RuntimeError("内部路径")),
        )
        == 1
    )

    assert status_errors == ["FATAL: reindex 维护窗口未就绪；安全状态未证明"]
    assert prepare_errors == ["FATAL: reindex 维护准备失败；安全状态未证明"]


def test_维护命令注册provision动作() -> None:
    from codev_platform.cli import build_parser

    args = build_parser().parse_args(["reindex-maintenance", "provision", "--yes"])

    assert args.action == "provision"
    assert args.yes is True


def test_维护命令注册受控CodeGraph恢复与配置动作() -> None:
    from codev_platform.cli import build_parser

    target = "a" * 40
    resume = build_parser().parse_args(
        [
            "reindex-maintenance",
            "resume-codegraph",
            "--project",
            "demo",
            "--target-commit",
            target,
            "--yes",
        ]
    )
    configure = build_parser().parse_args(
        [
            "reindex-maintenance",
            "configure-resume-codegraph",
            "--project",
            "demo",
            "--target-commit",
            target,
            "--yes",
        ]
    )

    assert (resume.action, resume.project, resume.target_commit, resume.yes) == (
        "resume-codegraph",
        "demo",
        target,
        True,
    )
    assert (configure.action, configure.project, configure.target_commit, configure.yes) == (
        "configure-resume-codegraph",
        "demo",
        target,
        True,
    )


def test_受控CodeGraph恢复演练不导入或委派状态机() -> None:
    from codev_platform.ops.reindex_maintenance import cmd_reindex_maintenance

    output: list[str] = []
    assert (
        cmd_reindex_maintenance(
            SimpleNamespace(
                action="resume-codegraph",
                project="demo",
                target_commit="a" * 40,
                yes=False,
            ),
            out=output.append,
            err=lambda _message: pytest.fail("演练不应报错"),
            resume_runner=lambda *_args: pytest.fail("演练不得恢复 CodeGraph"),
        )
        == 0
    )

    assert output == ["演练：resume-codegraph 会受控恢复 CodeGraph；确认后请添加 --yes"]


@pytest.mark.parametrize(
    "action",
    ["resume-codegraph", "configure-resume-codegraph"],
)
def test_受控CodeGraph动作缺少明确项目或完整目标不得委派(action: str) -> None:
    from codev_platform.ops.reindex_maintenance import cmd_reindex_maintenance

    errors: list[str] = []
    assert (
        cmd_reindex_maintenance(
            SimpleNamespace(action=action, project=None, target_commit="short", yes=True),
            out=lambda _message: pytest.fail("参数无效不得报告完成"),
            err=errors.append,
            resume_runner=lambda *_args: pytest.fail("参数无效不得恢复"),
            configure_resume_runner=lambda *_args: pytest.fail("参数无效不得配置"),
        )
        == 1
    )

    assert errors == ["FATAL: CodeGraph 恢复要求完整项目标识与目标提交"]


def test_受控CodeGraph恢复确认后只委派窄恢复叶子() -> None:
    from codev_platform.ops.reindex_maintenance import cmd_reindex_maintenance

    target = "a" * 40
    calls: list[tuple[str, str]] = []
    output: list[str] = []
    assert (
        cmd_reindex_maintenance(
            SimpleNamespace(
                action="resume-codegraph", project="demo", target_commit=target, yes=True
            ),
            out=output.append,
            err=lambda _message: pytest.fail("恢复不应报错"),
            resume_runner=lambda project, commit: calls.append((project, commit)),
        )
        == 0
    )

    assert calls == [("demo", target)]
    assert output == ["CodeGraph 受控恢复完成"]


def test_受控CodeGraph配置确认后只委派窄配置叶子() -> None:
    from codev_platform.ops.reindex_maintenance import cmd_reindex_maintenance

    target = "a" * 40
    calls: list[tuple[str, str]] = []
    output: list[str] = []
    assert (
        cmd_reindex_maintenance(
            SimpleNamespace(
                action="configure-resume-codegraph",
                project="demo",
                target_commit=target,
                yes=True,
            ),
            out=output.append,
            err=lambda _message: pytest.fail("配置不应报错"),
            configure_resume_runner=lambda project, commit: calls.append((project, commit)),
        )
        == 0
    )

    assert calls == [("demo", target)]
    assert output == ["CodeGraph 受管恢复配置完成"]


def test_受控CodeGraph恢复失败只报告受控中文安全状态() -> None:
    from codev_platform.ops.reindex_codegraph_resume import CodegraphResumeError
    from codev_platform.ops.reindex_maintenance import cmd_reindex_maintenance

    errors: list[str] = []
    assert (
        cmd_reindex_maintenance(
            SimpleNamespace(
                action="resume-codegraph",
                project="demo",
                target_commit="a" * 40,
                yes=True,
            ),
            out=lambda _message: pytest.fail("失败不得报告完成"),
            err=errors.append,
            resume_runner=lambda *_args: (_ for _ in ()).throw(
                CodegraphResumeError("CodeGraph 恢复失败；已回到维护状态")
            ),
        )
        == 1
    )

    assert errors == ["FATAL: CodeGraph 恢复失败；已回到维护状态"]


def test_受控CodeGraph配置失败不泄露异常类别或内容() -> None:
    from codev_platform.ops.reindex_maintenance import cmd_reindex_maintenance

    errors: list[str] = []
    assert (
        cmd_reindex_maintenance(
            SimpleNamespace(
                action="configure-resume-codegraph",
                project="demo",
                target_commit="a" * 40,
                yes=True,
            ),
            out=lambda _message: pytest.fail("失败不得报告完成"),
            err=errors.append,
            configure_resume_runner=lambda *_args: (_ for _ in ()).throw(
                RuntimeError("不应向终端暴露的内部细节")
            ),
        )
        == 1
    )

    assert errors == ["FATAL: CodeGraph 受管恢复配置失败"]


def test_受控CodeGraph配置编排失败报告已证明的中文安全状态() -> None:
    from codev_platform.ops.reindex_codegraph_resume_configuration import (
        CodegraphResumeConfigurationError,
    )
    from codev_platform.ops.reindex_maintenance import cmd_reindex_maintenance

    errors: list[str] = []
    assert (
        cmd_reindex_maintenance(
            SimpleNamespace(
                action="configure-resume-codegraph",
                project="demo",
                target_commit="a" * 40,
                yes=True,
            ),
            out=lambda _message: pytest.fail("失败不得报告完成"),
            err=errors.append,
            configure_resume_runner=lambda *_args: (_ for _ in ()).throw(
                CodegraphResumeConfigurationError(
                    "CodeGraph 受管恢复配置失败；已回到维护状态"
                )
            ),
        )
        == 1
    )

    assert errors == ["FATAL: CodeGraph 受管恢复配置失败；已回到维护状态"]


def test_systemd布局迁移命令要求绝对manifest和显式确认() -> None:
    from codev_platform.cli import build_parser

    args = build_parser().parse_args(
        [
            "reindex-maintenance",
            "migrate-systemd-layout",
            "--manifest",
            "/home/tester/codev-systemd/install-manifest.json",
            "--yes",
        ]
    )

    assert args.action == "migrate-systemd-layout"
    assert args.manifest == Path("/home/tester/codev-systemd/install-manifest.json")
    assert args.yes is True


def test_systemd布局迁移演练不读取manifest也不委派() -> None:
    from codev_platform.ops.reindex_maintenance import cmd_reindex_maintenance

    output: list[str] = []
    assert (
        cmd_reindex_maintenance(
            SimpleNamespace(
                action="migrate-systemd-layout",
                manifest=Path("/home/tester/codev-systemd/install-manifest.json"),
                yes=False,
            ),
            out=output.append,
            err=lambda _message: pytest.fail("演练不应报错"),
            layout_migration_runner=lambda _path: pytest.fail("演练不得迁移布局"),
        )
        == 0
    )

    assert output == ["演练：migrate-systemd-layout 会迁移 CodeGraph 主 unit；确认后请添加 --yes"]


def test_systemd布局迁移确认后只委派窄事务且不泄露内部异常() -> None:
    from codev_platform.ops.reindex_maintenance import cmd_reindex_maintenance

    manifest = Path("/home/tester/codev-systemd/install-manifest.json")
    calls: list[Path] = []
    output: list[str] = []
    assert (
        cmd_reindex_maintenance(
            SimpleNamespace(action="migrate-systemd-layout", manifest=manifest, yes=True),
            out=output.append,
            err=lambda _message: pytest.fail("迁移不应报错"),
            layout_migration_runner=calls.append,
        )
        == 0
    )
    assert calls == [manifest]
    assert output == ["CodeGraph systemd 主 unit 布局迁移完成"]

    errors: list[str] = []
    assert (
        cmd_reindex_maintenance(
            SimpleNamespace(action="migrate-systemd-layout", manifest=Path("relative.json"), yes=True),
            out=lambda _message: pytest.fail("相对路径不得报告完成"),
            err=errors.append,
            layout_migration_runner=lambda _path: pytest.fail("相对路径不得委派"),
        )
        == 1
    )
    assert errors == ["FATAL: systemd 布局迁移要求绝对 manifest 路径"]
