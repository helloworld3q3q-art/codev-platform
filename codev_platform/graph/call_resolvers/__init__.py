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
from codev_platform.graph.call_resolvers import codegraph as _codegraph

_codegraph.register_into()

__all__ = [
    "CallResolver",
    "applicable_resolvers",
    "register_resolver",
    "registered_resolvers",
]
