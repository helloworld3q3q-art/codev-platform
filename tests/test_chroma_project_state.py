"""chroma server.py 拆出 _project_state.py 后的拆分红线断言 (审计补测, 把手动核查转成测试)。

拆分风险: server 与 _project_state 若各持一份 _projects/_ensure_project 副本, 多租户状态
就会不一致。re-export 必须是**同一对象**。另: _project_state 只依赖叶子模块, 不得 import
server (否则 _models→_project_state→server 成环)。
"""
from __future__ import annotations

import importlib
import inspect


def test_project_state_reexport_same_objects():
    # server re-export 的符号必须是 _project_state 的同一对象 (非副本)。
    from codev_platform.chroma import _project_state as ps
    from codev_platform.chroma import server as s

    assert s._projects is ps._projects
    assert s._ensure_project is ps._ensure_project
    assert s._load_project_state is ps._load_project_state
    assert s._maybe_reload_project is ps._maybe_reload_project
    assert s._project_last_indexed_iso is ps._project_last_indexed_iso
    assert s._ProjectState is ps._ProjectState


def test_project_state_no_server_import():
    # 无环: _project_state 的**实际 import 语句**不得引用 server (docstring 里的 re-export 示例文字不算)。
    import ast

    mod = importlib.import_module("codev_platform.chroma._project_state")
    tree = ast.parse(inspect.getsource(mod))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
        elif isinstance(node, ast.Import):
            modules.extend(a.name for a in node.names)
    offenders = [m for m in modules if "chroma.server" in m or m.endswith(".server")]
    assert not offenders, f"_project_state 不应 import server (会成环): {offenders}"
