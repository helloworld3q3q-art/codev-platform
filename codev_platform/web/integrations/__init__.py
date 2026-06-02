"""web.integrations —— 封装对 codegraph/cross_link/chroma/agent/reindex 的调用 (plan §二)。

把外部异常转领域错误 (PlatformError); 资源隔离铁律 (plan §12.1): 只读直连 SQLite 或走既有
daemon + 有界并发 + timeout, 绝不本地起 embedding/reranker 模型。
"""
