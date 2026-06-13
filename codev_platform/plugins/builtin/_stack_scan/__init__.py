"""通用栈插件共享扫描工具 (FrontendReact / FastApi / Node / Spring / Vue 复用)。

把 tools/cross_link/build_codev.py 里一次性、绑死 codev-platform 目录布局的扫描思路
抽成**与项目无关**的纯函数: 给定 repo 根, 探测技术栈 + 扫出 frontend/backend 对象,
产出统一 graph.schema 的 GraphNode/GraphEdge。任意 React / FastAPI 仓都能复用,
不再 per-project 写脚本。

设计要点:
- detect 基于 **repo 内容** (package.json deps / *.tsx 存在 / import fastapi / @router 装饰器),
  不基于项目名或固定目录名 (web-ui / codev_platform 都不写死)。
- node id 统一 "<project_id>:<kind>:<stable-key>" (与 builtin.sql / 其它栈插件同构,
  保证跨插件可链接)。
- URL 匹配建 calls_api 边的逻辑集中在 link_api_calls() 一处 (单一真值源)。
- 不吞异常返回空: 单文件 parse / read 失败记 warning 并跳过该文件, 不中断整体。

> 2026-06-04 结构拆分: 原 863 行单文件按栈拆为本包 (_common / react / vue /
> fastapi / node / spring / sql / _link / frontend_deps / url_registry / api_usage)。
> 2026-06-13 (Phase 9): **收窄本 __init__ re-export** —— 只保留真有跨模块消费方的符号
> (插件壳经 `_stack_scan.<fn>` 访问的 scan_* / *_detect / 文件遍历原语 + link_api_calls)。
> 各栈私有正则/解析内部 (_RE_* / _spring_* / _NODE_* ...) 不再从顶层 re-export —— 无外部
> 消费方, 子模块各自 `from .<mod> import` 直取, 删 ~30 个死门面 (审计实证: 全仓无 `_stack_scan.
> <私有符号>` 访问, 测试只 import 公共 scan_* / link_api_calls / scan_url_registry)。
"""
from __future__ import annotations

# 文件遍历 / 路径 / url 归一原语 (_common): 插件壳与各栈扫描经 `_stack_scan.<fn>` 复用。
from ._common import _SKIP_DIRS, _iter_files, _norm_url, _rel
# 跨层 link (前端 api_call -> 后端 endpoint URL 匹配, 单一真值源)。
from ._link import link_api_calls
# 前端组件依赖图 (dependency-cruiser 接入)。
from .frontend_deps import scan_frontend_deps
# 前端 API url 注册文件提取 (代码基础层)。
from .url_registry import scan_url_registry
# 各栈 detect + scan (插件壳经 _stack_scan.<fn> 调)。
from .fastapi import fastapi_detect, scan_fastapi
from .node import node_detect, scan_node_express
from .react import react_detect, scan_react, scan_react_pages
from .spring import scan_spring, spring_detect
from .vue import scan_vue, scan_vue_routes, vue_detect

__all__ = [
    # _common 原语
    "_SKIP_DIRS",
    "_iter_files",
    "_norm_url",
    "_rel",
    # link
    "link_api_calls",
    # 前端组件依赖图
    "scan_frontend_deps",
    # 前端 API url 注册文件提取
    "scan_url_registry",
    # react
    "react_detect",
    "scan_react",
    "scan_react_pages",
    # vue
    "vue_detect",
    "scan_vue",
    "scan_vue_routes",
    # fastapi
    "fastapi_detect",
    "scan_fastapi",
    # node
    "node_detect",
    "scan_node_express",
    # spring
    "spring_detect",
    "scan_spring",
]
