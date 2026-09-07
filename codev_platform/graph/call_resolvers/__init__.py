"""调用边解析器包:框架(base) + 内置 resolver 注册。

加语言栈 resolver:① 写 codev_platform/graph/call_resolvers/<lang>.py 实现 CallResolver
② 在本文件 import + register_into。核心(ingest)只调 applicable_resolvers, 不关心有哪些。
"""
from codev_platform.graph.call_resolvers.base import (
    CallResolver,
    applicable_resolvers,
    register_resolver,
    registered_resolvers,
)

# 内置 resolver 注册(加语言 = 加一行 import + register_into)。
# 顺序 = confidence 并列 tiebreak: codegraph(跨语言兜底, conf 0.7)先注册, 各语言专门
# resolver(精确解析 conf 更高)后注册 —— 同边按 confidence 取胜, 专门者正确盖过兜底。
from codev_platform.graph.call_resolvers import codegraph as _codegraph
from codev_platform.graph.call_resolvers import fastapi as _fastapi

_codegraph.register_into()
_fastapi.register_into()

__all__ = [
    "CallResolver",
    "applicable_resolvers",
    "register_resolver",
    "registered_resolvers",
]
