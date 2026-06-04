"""通用栈插件共享扫描工具 (FrontendReact / FastApi 复用)。

把 tools/cross_link/build_codev.py 里一次性、绑死 codev-platform 目录布局的扫描思路
抽成**与项目无关**的纯函数: 给定 repo 根, 探测技术栈 + 扫出 frontend/backend 对象,
产出统一 graph.schema 的 GraphNode/GraphEdge。任意 React / FastAPI 仓都能复用,
不再 per-project 写脚本。

设计要点:
- detect 基于 **repo 内容** (package.json deps / *.tsx 存在 / import fastapi / @router 装饰器),
  不基于项目名或固定目录名 (web-ui / codev_platform 都不写死)。
- node id 统一 "<project_id>:<kind>:<stable-key>" (与 graph/adapters/cross_link 同构,
  保证跨插件可链接)。
- URL 匹配建 calls_api 边的逻辑集中在 link_api_calls() 一处 (单一真值源)。
- 不吞异常返回空: 单文件 parse / read 失败记 warning 并跳过该文件, 不中断整体。

> 2026-06-04 结构拆分: 原 863 行单文件按栈拆为本包 (_common / react / vue /
> fastapi / node / spring / _link), 纯结构移动, 零逻辑改动。本 __init__ re-export
> 全部 public + helper, 保持 `from ... import _stack_scan; _stack_scan.<fn>` 旧用法不变。
"""
from __future__ import annotations

from ._common import (
    _SKIP_DIRS,
    _RE_INLINE_URL,
    _has_file_with_suffix,
    _iter_files,
    _iter_named,
    _norm_url,
    _rel,
    _scan_inline_api,
    _walk_pruned,
    logger,
)
from ._link import link_api_calls
from .fastapi import (
    _HTTP_METHODS,
    _route_from_decorator,
    fastapi_detect,
    scan_fastapi,
)
from .node import (
    _NODE_BACKEND_DEPS,
    _NODE_ROUTE_OBJ,
    _RE_NODE_ROUTE,
    node_detect,
    scan_node_express,
)
from .react import (
    _HTTP_METHOD_PREFIX,
    _RE_FN,
    _RE_URL,
    _infer_method,
    react_detect,
    scan_react,
    scan_react_pages,
)
from .spring import (
    _JAVA_KW,
    _RE_JAVA_TYPEDECL,
    _RE_SPRING_CONTROLLER,
    _RE_SPRING_MAPPING,
    _SPRING_METHOD_ANN,
    _join_url,
    _spring_ann_path,
    _spring_class_base,
    _spring_class_pos,
    _spring_handler_name,
    _spring_req_method,
    scan_spring,
    spring_detect,
)
from .vue import (
    _RE_VUE_ROUTE,
    _RE_VUE_ROUTE_COMP,
    _RE_VUE_ROUTE_NAME,
    scan_vue,
    scan_vue_routes,
    vue_detect,
)

__all__ = [
    # common helpers
    "_SKIP_DIRS",
    "_RE_INLINE_URL",
    "_has_file_with_suffix",
    "_iter_files",
    "_iter_named",
    "_norm_url",
    "_rel",
    "_scan_inline_api",
    "_walk_pruned",
    "logger",
    # link
    "link_api_calls",
    # react
    "react_detect",
    "scan_react",
    "scan_react_pages",
    "_infer_method",
    "_RE_FN",
    "_RE_URL",
    "_HTTP_METHOD_PREFIX",
    # vue
    "vue_detect",
    "scan_vue",
    "scan_vue_routes",
    "_RE_VUE_ROUTE",
    "_RE_VUE_ROUTE_NAME",
    "_RE_VUE_ROUTE_COMP",
    # fastapi
    "fastapi_detect",
    "scan_fastapi",
    "_route_from_decorator",
    "_HTTP_METHODS",
    # node
    "node_detect",
    "scan_node_express",
    "_NODE_BACKEND_DEPS",
    "_RE_NODE_ROUTE",
    "_NODE_ROUTE_OBJ",
    # spring
    "spring_detect",
    "scan_spring",
    "_SPRING_METHOD_ANN",
    "_RE_SPRING_CONTROLLER",
    "_RE_SPRING_MAPPING",
    "_JAVA_KW",
    "_RE_JAVA_TYPEDECL",
    "_spring_ann_path",
    "_spring_req_method",
    "_join_url",
    "_spring_class_pos",
    "_spring_class_base",
    "_spring_handler_name",
]
