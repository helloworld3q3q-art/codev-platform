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

# 内置 analyzer 注册。LLM 业务域 analyzer(A1)A1-3 验收通过(codev-platform 真 deepseek
# 实测 ~95%), 经 **config gate 显式开启**才注册 —— 默认关, 避免无意 ingest 触发 LLM +
# reindex 成本 + 测试误触发。开启: config.analyzers.business_domain.enabled = true
# (且 provider 有 key, 否则 applies 仍 no-op)。


def _register_configured(cfg: dict | None = None) -> int:
    """按 config.analyzers.<name>.enabled 决定注册哪些 LLM analyzer。

    默认全关; 显式 true 才注册(applies 再查 labeler.available, 无 key 仍 no-op)。返回注册数。
    A1 业务域(实测 ~95%)/ A2 架构分层各自 config gate, 互不依赖。
    """
    from codev_platform.core.config import get, load_config

    cfg = cfg if cfg is not None else load_config()
    n = 0
    if get(cfg, "analyzers.business_domain.enabled", False):
        from codev_platform.graph.analyzers.brain_labeler import BrainDomainLabeler
        from codev_platform.graph.analyzers.business_domain import BusinessDomainAnalyzer
        register_analyzer(BusinessDomainAnalyzer(BrainDomainLabeler()))
        n += 1
    if get(cfg, "analyzers.arch_layer.enabled", False):
        from codev_platform.graph.analyzers.architecture_layer import ArchLayerAnalyzer
        from codev_platform.graph.analyzers.brain_layer_labeler import BrainLayerLabeler
        register_analyzer(ArchLayerAnalyzer(BrainLayerLabeler()))
        n += 1
    # A3 前端 API 链接(LLM 补静态层盲区): 需 repo_path 读源码, 经 _analyzers_pass 的
    # set_context 注入(注册时 repo 未知, applies 在无 repo 时返 False = no-op)。
    if get(cfg, "analyzers.frontend_api_link.enabled", False):
        from codev_platform.graph.analyzers.brain_api_link_labeler import BrainApiLinkLabeler
        from codev_platform.graph.analyzers.frontend_api_link import FrontendApiLinkAnalyzer
        register_analyzer(FrontendApiLinkAnalyzer(BrainApiLinkLabeler()))
        n += 1
    return n


try:
    _register_configured()
except Exception:  # noqa: BLE001 — config/provider 不可用 → 不注册(no-op), 不拖垮 import
    pass

__all__ = [
    "Analyzer",
    "applicable_analyzers",
    "register_analyzer",
    "registered_analyzers",
    "validate_soft_result",
]
