"""codev_platform.chroma — chroma 多租户 MCP daemon + BM25 hybrid recall。

模块组织:
    server   chroma daemon (MCP SSE server, multi-tenant routing via X-Project-Id / ?project_id=)
    bm25     BM25 倒排索引 (jieba + rank_bm25), 与 chroma 向量召回做 RRF 融合

入口: python -m codev_platform.chroma.server [--http] [--port 18083]
依赖: chromadb, sentence-transformers (Qwen3-Embedding-0.6B), torch, optional jieba+rank_bm25
"""
