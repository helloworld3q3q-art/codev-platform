"""嵌入模型层(可换模型)。

Embedder 抽象在 agent/memory_vector.py(召回接缝);本包提供 **registry + 各 backend adapter**:
换更好的模型 = `register_embedder("bge"/"openai"/"remote", ...)` 一个 adapter + config `memory.embed.backend`
改一处,不动召回逻辑 / 不动 ChromaMemoryVectorIndex。
- qwen.py     QwenLocalEmbedder(本地 sentence-transformers,device 可配)
- registry.py register_embedder + build_embedder(cfg)(零 if-else,缺依赖 → None 降级)
"""
