# chroma 检索 daemon 跨平台落地:Mac 运行时(2026-05-29)

> **动机**:`feat/xplatform-cli` 把运维编排(.ps1)跨平台化了,但 **AI 检索能力(chroma MCP daemon)这一层仍是纯 Windows/CUDA**。launcher 写死 `Scripts/.exe`,`.mcp.json` 用 `cmd`,config 默认 `cuda` + `D:\models\...`。队友的 Mac 上整套检索不可用。
>
> **目标**:让 chroma 检索 daemon 在 Apple Silicon Mac 上跑起来,并接入 Claude Code。
>
> **硬约束**:① 不破坏现有 Windows 机(launcher 改动全 `sys.platform` 条件化);② 路径配置化,零硬编码(延续 `core.config` / `~` 展开);③ 本地 commit,不 push。

---

## 决策(brainstorm 已确认)

| 项 | 决策 | 理由 |
|---|---|---|
| 实施节奏 | **两步**:先 L1–L4 独立验证,再 L5 接 Claude | 风险前置在不碰共享代码的阶段 |
| 模型来源 | **手动下载到本地** `~/models/Qwen3-Embedding-0.6B` | 零代码改动,路径语义同 Windows |
| 推理设备 | **`mps`**(本机 arm64),代码已有 GPU→CPU 降级兜底 | Apple Silicon,0.6B 模型 mps 够快 |
| reranker | **第一步先关**(`reranker_enabled: false`) | 搜索质量增强项,验证阶段非必需,省一个模型 |

---

## 第一步:L1–L4 — daemon 独立可检索

仓内代码**不改**;改动落在本机环境 + 用户级 config。

| 层 | 动作 | 验证 |
|---|---|---|
| L1 运行时 | `pip install -e ".[runtime]"`(arm64 自动取 Mac 版 torch) | `import chromadb, sentence_transformers, torch` OK;`torch.backends.mps.is_available()` → True |
| L2 模型 | `hf download Qwen/Qwen3-Embedding-0.6B --local-dir ~/models/Qwen3-Embedding-0.6B`(huggingface_hub 随 L1 装入) | 目录含 `config.json` + 权重 |
| L3 配置 | 新建 `~/.codev-platform/config.json`(见下) | `codev-platform config show` → `exists=True` + Mac 路径 |
| L4 索引 | `python -m codev_platform.chroma.indexer --force` | 写入 chunk 数 > 0;`data/chroma/` 出现 |

**L3 config**(只覆盖 Mac 该改的,余走默认):
```json
{
  "models": {
    "embed_path": "~/models/Qwen3-Embedding-0.6B",
    "embed_device": "mps",
    "reranker_enabled": false
  },
  "data": { "platform_data_dir": null }
}
```

**第一步完成判据**:
1. `python -m codev_platform.chroma.server --http` 启动无异常,`curl 127.0.0.1:18083/health` → green;
2. 对 indexed collection 跑一条检索,返回相关 chunk。

**已知风险**:`mps` 上 SentenceTransformer 偶有算子缺失;代码内 GPU 失败降级只认 cuda 关键词,mps 异常可能不被捕获。回退:config 改 `embed_device: cpu`(实测时确认)。

---

## 第二步:L5 — 接入 Claude Code(代码改动)

三处 Windows 写死,全部 `sys.platform` 条件化,**不破坏 Windows**:

1. **`launcher.py`**:`VENV_SCRIPTS = _VENV / ("Scripts" if win else "bin")`;`python.exe`/`pythonw.exe`/`mcp-proxy.exe` 在非 win 去 `.exe`。(creationflags 段已条件化,照同模式。)
2. **`.mcp.json`**:现为 `cmd /c *.cmd`。跨平台方案(committed 文件需同服两端)在第一步跑通后定稿——倾向直接 `python -m codev_platform.chroma.launcher`,省 shell 壳。
3. **`.cmd` 脚本**:配等价 `.sh`,或随方案 2 取消。

**第二步完成判据**:新开 Claude Code 会话,MCP `platform-docs` 连得上,`search_docs` 返回结果。

---

## 不做(YAGNI)
- 不动 cross_link MCP(本次只解决 chroma 检索)。
- 不开 reranker(后续单独验证 mps 可用性再开)。
- 不改 Windows 既有路径行为(仅新增非 win 分支)。
