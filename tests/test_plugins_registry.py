"""plugins runtime 测试 — 注册 / 列出 / 未知报错 / 执行隔离 / 结果校验.

不触网 / 不读真实仓:用假插件 (FakePlugin / CrashPlugin / BadResultPlugin) 覆盖
registry + executor 的全部分支。每个测试自清注册表,避免污染进程级单例。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from codev_platform.graph.schema import AnalyzerResult, GraphNode
from codev_platform.plugins import (
    ERR_ANALYZE_FAILED,
    ERR_DETECT_FAILED,
    ERR_INVALID_RESULT,
    ERR_NOT_APPLICABLE,
    clear_registry,
    get_plugin,
    list_plugins,
    register_plugin,
    registered_names,
    run_all,
    run_applicable,
)
from codev_platform.plugins.base import AnalyzerPlugin, AnalyzerPluginProtocol
from codev_platform.plugins.executor import run_plugin


# ---- 假插件 ----

class FakePlugin(AnalyzerPlugin):
    name = "fake.ok"
    version = "1.2.3"

    def __init__(self, applicable: bool = True):
        self._applicable = applicable

    def detect(self, repo_path: Path) -> bool:
        return self._applicable

    def analyze(self, repo_path: Path, project_id: str) -> AnalyzerResult:
        node = GraphNode(id=f"{project_id}:project:root", kind="project",
                         name="root", project_id=project_id)
        return AnalyzerResult(nodes=[node])


class CrashPlugin(AnalyzerPlugin):
    name = "fake.crash"
    version = "0.1.0"

    def detect(self, repo_path: Path) -> bool:
        return True

    def analyze(self, repo_path: Path, project_id: str) -> AnalyzerResult:
        raise RuntimeError("boom inside analyze")


class DetectCrashPlugin(AnalyzerPlugin):
    name = "fake.detect_crash"
    version = "0.1.0"

    def detect(self, repo_path: Path) -> bool:
        raise OSError("boom inside detect")

    def analyze(self, repo_path: Path, project_id: str) -> AnalyzerResult:  # pragma: no cover
        return AnalyzerResult()


class BadResultPlugin(AnalyzerPlugin):
    name = "fake.bad_result"
    version = "0.1.0"

    def detect(self, repo_path: Path) -> bool:
        return True

    def analyze(self, repo_path: Path, project_id: str):
        return {"nodes": []}  # 不是 AnalyzerResult -> INVALID_RESULT


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


# ---- registry: 注册 / 列出 / 未知报错 ----

def test_register_and_list():
    register_plugin(FakePlugin())
    assert "fake.ok" in registered_names()
    # list_plugins 触发 _discover_builtins (注入 builtin.*); 只校验本测试注册的假插件存在
    names = [p.name for p in list_plugins()]
    assert "fake.ok" in names


def test_list_sorted():
    register_plugin(CrashPlugin())
    register_plugin(FakePlugin())
    # 内置插件 (builtin.*) 也会被发现; 只校验假插件子集按 name 升序稳定
    fakes = [n for n in registered_names() if n.startswith("fake.")]
    assert fakes == ["fake.crash", "fake.ok"]


def test_get_unknown_raises():
    with pytest.raises(KeyError):
        get_plugin("does.not.exist")


def test_register_without_name_raises():
    class NoName(AnalyzerPlugin):
        name = ""
        version = "1"

        def detect(self, repo_path):  # pragma: no cover
            return True

        def analyze(self, repo_path, project_id):  # pragma: no cover
            return AnalyzerResult()

    with pytest.raises(ValueError):
        register_plugin(NoName())


def test_protocol_runtime_check():
    assert isinstance(FakePlugin(), AnalyzerPluginProtocol)


# ---- executor: 执行隔离 + 结果校验 ----

def test_run_plugin_ok(tmp_path):
    r = run_plugin(FakePlugin(), tmp_path, "demo")
    assert r.ok and r.result is not None
    assert r.summary["nodes"] == 1
    assert r.result.plugin == "fake.ok" and r.result.plugin_version == "1.2.3"
    assert r.error_code is None


def test_analyze_crash_isolated(tmp_path):
    # 假插件 analyze 抛异常 —— 不崩,降级成带错误码的 ExecutionResult
    r = run_plugin(CrashPlugin(), tmp_path, "demo")
    assert not r.ok
    assert r.error_code == ERR_ANALYZE_FAILED
    assert "boom inside analyze" in r.error
    assert r.result is None


def test_detect_crash_isolated(tmp_path):
    r = run_plugin(DetectCrashPlugin(), tmp_path, "demo")
    assert not r.ok and r.error_code == ERR_DETECT_FAILED


def test_not_applicable(tmp_path):
    r = run_plugin(FakePlugin(applicable=False), tmp_path, "demo")
    assert not r.ok and r.error_code == ERR_NOT_APPLICABLE


def test_invalid_result_type(tmp_path):
    r = run_plugin(BadResultPlugin(), tmp_path, "demo")
    assert not r.ok and r.error_code == ERR_INVALID_RESULT


def test_run_detect_skip(tmp_path):
    # run_detect=False 时即便 detect 会崩也不调,直接 analyze
    r = run_plugin(DetectCrashPlugin.__new__(DetectCrashPlugin), tmp_path, "demo",
                   run_detect=False)
    # DetectCrashPlugin.analyze 返回空 AnalyzerResult
    assert r.ok and r.summary["nodes"] == 0


# ---- registry 批量执行:一个崩了不影响其余 ----

def test_run_all_isolation(tmp_path):
    register_plugin(FakePlugin())
    register_plugin(CrashPlugin())
    results = run_all(tmp_path, "demo")
    by_name = {r.plugin: r for r in results}
    assert by_name["fake.ok"].ok is True
    assert by_name["fake.crash"].ok is False
    assert by_name["fake.crash"].error_code == ERR_ANALYZE_FAILED


def test_run_applicable_filters(tmp_path):
    register_plugin(FakePlugin())                       # ok
    register_plugin(CrashPlugin())                      # analyze 崩
    register_plugin(FakePlugin(applicable=False))       # 同名覆盖 fake.ok -> 不适用
    register_plugin(BadResultPlugin())                  # 结果非法
    ok_results = run_applicable(tmp_path, "demo")
    # 都过滤掉: fake.ok 被覆盖成 not-applicable, crash/bad 都失败
    assert all(r.ok for r in ok_results)
    assert "fake.crash" not in {r.plugin for r in ok_results}
    assert "fake.bad_result" not in {r.plugin for r in ok_results}
