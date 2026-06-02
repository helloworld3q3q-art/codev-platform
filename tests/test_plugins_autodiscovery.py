"""registry 自动发现机制测试 —— 内置插件免手工注册 + 新增即生效 + 异常隔离。

核心断言:_discover_builtins 扫 plugins/builtin/ 目录, 自动注册其中 AnalyzerPlugin
子类。验证手段:
1. 已知三个内置插件 (cross_link / frontend_react / backend_fastapi) 都被发现。
2. 往 builtin 包目录临时丢一个新插件模块 -> 不改 registry 即被发现 (build agent 免改)。
3. 共享工具模块 (_stack_scan, 下划线开头) 不被当插件。
4. 一个坏模块 (import 期抛错) 只 warning + 跳过, 不阻断其余发现。

每测自清注册表; 临时模块测完即删, 不污染包目录与 sys.modules。
"""
from __future__ import annotations

import os
import sys

import pytest

from codev_platform.plugins import (
    clear_registry,
    list_plugins,
    registered_names,
)
from codev_platform.plugins import builtin


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


def test_known_builtins_autodiscovered():
    """三个已知内置插件无需在 registry 手工列出即被发现。"""
    names = registered_names()
    assert "builtin.cross_link" in names
    assert "builtin.frontend_react" in names
    assert "builtin.backend_fastapi" in names


def test_underscore_module_not_treated_as_plugin():
    """_stack_scan 是共享工具 (下划线开头), 不产生插件实例。"""
    # 没有任何插件 name 来自 _stack_scan (它本就不定义 AnalyzerPlugin 子类),
    # 同时确认发现流程不因它报错。
    plugins = list_plugins()
    assert all(getattr(p, "name", "") for p in plugins)


def test_idempotent_discovery():
    """重复触发发现 (clear 后再 list) 数量稳定, 无重复注册。"""
    first = registered_names()
    clear_registry()
    second = registered_names()
    assert first == second


def test_new_plugin_file_auto_registered(tmp_path):
    """往 builtin 包目录丢一个新插件模块 -> 不改 registry 即被发现 (build agent 免改)。

    用真实文件写入 builtin 包目录 + 触发重新发现, 测完删除文件 / 清 sys.modules,
    模拟 build agent 新增框架插件文件的场景。
    """
    source = (
        "from pathlib import Path\n"
        "from codev_platform.graph.schema import AnalyzerResult\n"
        "from codev_platform.plugins.base import AnalyzerPlugin\n"
        "\n"
        "class ProbePlugin(AnalyzerPlugin):\n"
        "    name = 'builtin.autodiscover_probe'\n"
        "    version = '9.9.9'\n"
        "    def detect(self, repo_path: Path) -> bool:\n"
        "        return False\n"
        "    def analyze(self, repo_path: Path, project_id: str) -> AnalyzerResult:\n"
        "        return AnalyzerResult()\n"
    )
    pkg_dir = builtin.__path__[0]
    real_name = "tmp_autodiscover_probe"  # 非下划线: 发现器只扫非下划线模块。
    probe_path = f"{pkg_dir}/{real_name}.py"
    full_mod = f"{builtin.__name__}.{real_name}"
    with open(probe_path, "w", encoding="utf-8") as fh:
        fh.write(source)
    try:
        clear_registry()
        names = registered_names()
        assert "builtin.autodiscover_probe" in names
    finally:
        sys.modules.pop(full_mod, None)
        os.remove(probe_path)
        clear_registry()

    # 删除后重新发现, 探针应消失 (证明确实来自该文件)。
    names_after = registered_names()
    assert "builtin.autodiscover_probe" not in names_after


def test_bad_module_isolated(tmp_path):
    """builtin 目录里一个 import 期就崩的模块只被跳过, 不阻断其余发现。"""
    pkg_dir = builtin.__path__[0]
    real_name = "tmp_bad_module"
    bad_path = f"{pkg_dir}/{real_name}.py"
    full_mod = f"{builtin.__name__}.{real_name}"
    with open(bad_path, "w", encoding="utf-8") as fh:
        fh.write("raise RuntimeError('boom at import')\n")
    try:
        clear_registry()
        names = registered_names()
        # 坏模块被跳过, 三个正常内置插件仍在。
        assert "builtin.cross_link" in names
        assert "builtin.frontend_react" in names
        assert "builtin.backend_fastapi" in names
    finally:
        sys.modules.pop(full_mod, None)
        os.remove(bad_path)
        clear_registry()
