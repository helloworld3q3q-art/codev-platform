"""综合分析器包:框架(base) + 内置 analyzer 注册。

加分析维度:① 写 codev_platform/graph/analyzers/<name>.py 实现 Analyzer
② 在本文件 import + register_analyzer。核心(ingest)只调 applicable_analyzers, 不关心有哪些。

当前内置: 无 —— 业务域 LLM analyzer(A1-2)待接入。框架先行(A1-1): ingest 的 _analyzers_pass
在无注册 analyzer 时 no-op, 生产无影响;软/硬隔离 + referential-integrity 校验已就绪可供它插入。
"""
from codev_platform.graph.analyzers.base import (
    Analyzer,
    applicable_analyzers,
    register_analyzer,
    registered_analyzers,
    validate_soft_result,
)

# 内置 analyzer 注册(加维度 = 加一行 import + register_analyzer)。
# 当前空: LLM business_domain analyzer 待 A1-2 接入(需 brain provider + grounding 设计)。

__all__ = [
    "Analyzer",
    "applicable_analyzers",
    "register_analyzer",
    "registered_analyzers",
    "validate_soft_result",
]
