# codev-platform chroma daemon 使用指南

把 `CLAUDE.md` / `.claude/rules/` / `docs/` / 子模块 CLAUDE.md 等 markdown 索引到本地 Chroma,通过 MCP 暴露给 Claude Code,做语义检索(中文 + 长 docstring 友好)。

## 架构

```
markdown -> codev_platform.chroma.indexer (三级 chunk + BM25)
                ↓
        Chroma PersistentClient (per-project collection)
        <project_id>__platform_docs
                ↓
        codev_platform.chroma.server (multi-tenant MCP daemon)
        3 tools: search_docs / list_collections / get_by_file
                ↓
        Claude Code (.mcp.json -> platform-docs-mcp.cmd -> launcher -> mcp-proxy)
```

## 配置

机器级:`~/.codev-platform/config.json`(详见 `config.example.json`)

| 字段 | 用途 |
|---|---|
| `models.embed_path` / `embed_device` | Qwen3-Embedding-0.6B(默认 cuda)|
| `models.reranker_path` / `reranker_device` | Qwen3-Reranker-0.6B |
| `data.platform_data_dir` | chroma DB 共享基目录 |
| `runtime.chroma_venv` | chroma .venv 路径(含 mcp-proxy.exe + python.exe)|
| `daemon.port` / `mode` / `prewarm` | daemon 启停 |
| `search.recall_k` / `return_k` / `bm25_enabled` / `rrf_k_const` / `gpu_concurrency` | 检索调参 |

项目级:`<repo>/.claude/index.json`

```json
{
  "doc_patterns": ["CLAUDE.md", "docs/**/*.md", "rules/*.md"],
  "external_doc_paths": ["D:/WorkSpace/codev-platform/rules/*.md"]
}
```

## 常用命令

```powershell
# 重建索引(本仓 cwd, 自动按 project_id 写到 <pid>__platform_docs)
$env:PLATFORM_ROOT = $pwd
C:\workspace\project -m codev_platform.chroma.indexer
# 加 --force 删 collection 重建; --dry-run 只数文件

# 检查 docs 子目录覆盖率(pre-push gate 6/6)
python -m codev_platform.chroma.audit_patterns

# 召回质量测试
python -m codev_platform.chroma.verify

# daemon 健康
curl https://example.invalid/reference

# daemon kill (单 listener,multi-tenant 模式无第二实例)
Get-NetTCPConnection -LocalPort 18083 | Select-Object -ExpandProperty OwningProcess | ForEach-Object { Stop-Process -Id $_ -Force }
```

## 三级 chunk 策略

`codev_platform.chroma.chunking.chunk_text`:

1. **heading 切**:H1/H2/H3,标题保留在段开头
2. **paragraph 贪心合并**:段超 `CHUNK_TARGET_MAX=1000` 按空行切并合并到目标区间
3. **hard split**:单段超 `CHUNK_HARD_MAX=1500` 字符强切

## BM25 hybrid

`codev_platform.chroma.bm25.BM25Index` + RRF 融合:

- jieba 中文分词 + ASCII 正则保留代码符号
- vector + BM25 两路 top-N → RRF k_const=60 融合
- env / 库缺失 / 索引未 build 都自动降级为纯向量

## 故障排查

| 现象 | 排查 |
|---|---|
| `Collection X not exists` | 跑 `-m codev_platform.chroma.indexer --force` 重建 |
| daemon 卡 60s | 检查 GPU 模型路径 + reranker 是否就绪;`prewarm=true` 让模型在启动期加载 |
| `mcp-proxy.exe not found` | `runtime.chroma_venv` 字段必须指向含 mcp-proxy 的 venv |
| 多 session 并发 P95 高 | `search.gpu_concurrency=1`(默认串行);8GB GPU 不要调高 |
| collection 命名错 | `.claude/project.json` 必须有 `project_id` 字段;`codev-platform current` 验证 |
