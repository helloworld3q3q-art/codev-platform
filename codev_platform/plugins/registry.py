"""插件注册表 + 发现机制 (Phase 2 plugin runtime: registry).

设计 (对齐 agent/brain/registry.py 风格 + decision doc §5 "第一批只做内置插件"):
    - register_plugin / list_plugins / get_plugin:声明式注册表,核心遍历它,零 if-else。
    - 发现机制:_discover_builtins() 显式导入并注册内置插件 —— 先不做第三方动态加载
      (entry_points / 目录扫描),保持可控、可测;放开第三方时只需扩展本函数。
    - run_all / run_applicable:对一个仓库批量执行插件,每个插件经 executor 隔离,
      单插件失败不影响其余 (plan 铁律:插件失败不拖垮核心)。

注册表是进程级单例 (模块级 _REGISTRY);_discover_builtins 幂等 (重复注册同名覆盖)。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from codev_platform.plugins.executor import ExecutionResult, run_plugin

_REGISTRY: dict[str, Any] = {}
_discovered = False


def register_plugin(plugin: Any) -> None:
    """注册一个插件实例 (按 name 入表;重名覆盖,允许替换内置实现)。"""
    name = getattr(plugin, "name", None)
    if not name:
        raise ValueError(f"plugin 缺少非空 name 属性: {plugin!r}")
    _REGISTRY[name] = plugin


def get_plugin(name: str) -> Any:
    """按 name 取插件;未知报错 (列出已注册名便于排查)。"""
    _ensure_discovered()
    plugin = _REGISTRY.get(name)
    if plugin is None:
        known = ", ".join(sorted(_REGISTRY)) or "(空)"
        raise KeyError(f"未知 plugin: {name!r}。已注册: {known}")
    return plugin


def list_plugins() -> list[Any]:
    """列出所有已注册插件实例 (按 name 排序,稳定输出)。"""
    _ensure_discovered()
    return [_REGISTRY[n] for n in sorted(_REGISTRY)]


def registered_names() -> list[str]:
    _ensure_discovered()
    return sorted(_REGISTRY)


def clear_registry() -> None:
    """清空注册表 (测试用;生产不调)。"""
    global _discovered
    _REGISTRY.clear()
    _discovered = False


def _discover_builtins() -> None:
    """显式注册内置插件 (decision doc §5: 第一批只内置,不做第三方动态加载)。

    放开第三方时:在此追加 importlib.metadata.entry_points("codev_platform.plugins")
    扫描 + 实例化,本函数即为唯一扩展点,核心其它代码无感。

    当前内置插件清单为空 (Phase 2 只落地 runtime);下一步把 cross-link 适配器包成
    首个内置插件 builtin.cross_link 时,在此 register_plugin(CrossLinkPlugin())。
    """
    # 示例 (待 cross-link 适配器就绪后启用):
    # from codev_platform.plugins.builtin.cross_link import CrossLinkPlugin
    # register_plugin(CrossLinkPlugin())
    return


def _ensure_discovered() -> None:
    global _discovered
    if not _discovered:
        _discovered = True
        _discover_builtins()


def run_all(repo_path: Path | str, project_id: str) -> list[ExecutionResult]:
    """对一个仓库执行所有已注册插件 (每个经 executor 隔离;先 detect 再 analyze)。

    返回每个插件的 ExecutionResult (含成功 / 失败 / 跳过),单插件崩溃不影响其余。
    """
    return [run_plugin(p, repo_path, project_id) for p in list_plugins()]


def run_applicable(repo_path: Path | str, project_id: str) -> list[ExecutionResult]:
    """只跑 detect() 命中的插件,返回成功结果 (跳过 NOT_APPLICABLE 与失败的)。

    便利封装:run_all 的子集,调用方常只关心"适用且成功"的产出。
    """
    out = []
    for r in run_all(repo_path, project_id):
        if r.ok:
            out.append(r)
    return out
