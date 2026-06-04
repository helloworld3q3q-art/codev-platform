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
# A1-2b 已就绪(BusinessDomainAnalyzer + BrainDomainLabeler), 但**暂不注册生产** ——
# 等 A1-3 验收(openclaw 10 endpoint 人工核对 domain 准确率 ≥70%)通过后再放开,
# 避免未验证的 LLM 标注进生产图谱(plan "先证方向 ≥70% 再投")。验收通过后取消下三行注释:
#   from codev_platform.graph.analyzers.brain_labeler import BrainDomainLabeler
#   from codev_platform.graph.analyzers.business_domain import BusinessDomainAnalyzer
#   register_analyzer(BusinessDomainAnalyzer(BrainDomainLabeler()))

__all__ = [
    "Analyzer",
    "applicable_analyzers",
    "register_analyzer",
    "registered_analyzers",
    "validate_soft_result",
]
