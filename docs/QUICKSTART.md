# QUICKSTART — 新机器 30 分钟跑通

> 目标:一台干净机器，从 clone 到「检索能搜出东西」全流程跑通。
> 命令逐条对齐真实 CLI（`codev-platform --help`）。安装细节见 [SETUP.md](./SETUP.md)，日常用法见 [USAGE.md](./USAGE.md)。
> 路径全走 `~/.codev-platform/config.json`，代码零盘符硬编码。

---

## 0. 前提

| 项 | 说明 |
|---|---|
| 仓 | `git clone` 本仓 codev-platform（自包含:venv / data / 模型 config 都在本仓周围） |
| Python | 3.11+ |
| GPU | **可选**。有 NVIDIA → Qwen embed + reranker；无卡 → 自动降级仓内 MiniLM fallback（纯 CPU 也能跑） |
| 模型 | 自备，不随仓下发。缺 embed 模型走 MiniLM fallback，缺 reranker 降级纯向量 + BM25 |

---

## 1. 装 (~5 min)

dev（改源码即生效）:

```bash
cd <parent>/codev-platform
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .                   # CLI + 核心可用(轻依赖)
```

重依赖（chroma / torch / 模型推理，**可选**，缺则检索降级）:

```bash
pip install -r requirements-runtime.txt
```

> 一把装齐:`codev-platform setup --auto`(建 venv + 探测模型 + 写 config + sync rules/skills)。Windows CUDA 轮子顺序见 SETUP.md §2。

验证 CLI 在 PATH:

```bash
codev-platform --version
```

打 wheel 发布(交付到别的机器):统一用 `pip wheel`,**不要用 `python -m build`**(后者在部分环境/无 `build` 包时不可用):

```bash
python -m pip wheel . -w dist --no-deps        # 产出 dist/codev_platform-*.whl
bash scripts/verify_clean_install.sh           # 干净 venv 装 wheel + 冒烟验证
```

---

## 2. 配 (~3 min)

生成用户级 config，再按本机改路径:

```bash
codev-platform config init         # 写默认 ~/.codev-platform/config.json
codev-platform config path         # 打印配置文件位置
codev-platform config show         # 看当前生效值(env > config > 默认)
```

`config.json` 关键段(样板见 `config.example.json`):

| 字段 | 含义 | 本地小卡 | 服务器大卡 | 无 GPU |
|---|---|---|---|---|
| `models.embed_path` | embed 模型目录 | `~/models/Qwen3-Embedding-0.6B` | 同左 | 留空 → MiniLM fallback |
| `models.embed_device` | 推理设备 | `cuda` | `cuda` / `cuda:0` | `cpu` |
| `models.embed_batch_size` | 索引吞吐(显存敏感) | `16` | 调大 | 小 |
| `models.reranker_path` | reranker(可选) | `~/models/Qwen3-Reranker-0.6B` | 同左 | 留空 → 纯向量+BM25 |
| `models.reranker_dtype` | 精度 | `auto` | `bfloat16`(Ampere+) | `float32` |
| `data.platform_data_dir` | 数据根(chroma + KG + memory) | 本仓 `data/` | 自定义 | 同左 |

> `setup --dry-run` 会先探测这些路径走哪条 fallback，不写盘。

---

## 3. 试 demo (~5 min)

新机器最快验证 = **索引本仓自己的文档**（codev-platform 项目，~290 chunks，真实可搜）:

```bash
cd <parent>/codev-platform              # 当前仓即 project_id=codev-platform
codev-platform current                  # 确认解析到的 project_id + 来源
python -m codev_platform.chroma.indexer --force    # 删旧 collection 全量重建本仓文档
```

> 对外公开演示用的「fake-but-real」demo 项目集（demo-crm / demo-wiki 等）是后续交付项（见 `docs/plans/roadmap-2026-06-01/pluginized-fullstack-ai-platform-2026-06-01.md` §demo）。在它落地前，用本仓自身文档当 demo 即可跑通全链路。

---

## 4. serve (~3 min)

拉起平台 MCP 端点(chroma daemon 预热 embedding ~30-60s):

```bash
codev-platform serve-mcp start          # 幂等拉起 cross-link + 各项目 codegraph 端点
```

或换机器/重 clone 后一键(venv 体检 + serve-mcp + codegraph link + memory):

```bash
codev-platform bootstrap                # 加 --dry-run 先看有序步骤
```

> chroma daemon 不由 `serve-mcp start` 拉起 —— 它由业务仓首次 Claude Code 会话经 launcher 自 spawn。本步只拉 cross-link / codegraph 端点 + 报 chroma 状态。

---

## 5. test query (~3 min)

验「检索真能搜出东西」三连:

```bash
codev-platform serve-mcp status         # 各端点 OK/DOWN + sse_url
codev-platform daemon status            # chroma daemon pid/uptime + 已加载项目 chunks
codev-platform health --mode light      # 工具栈体检 banner: READY / ATTENTION / BROKEN
```

完整探针(embed load + torch + collection 实查):

```bash
codev-platform health                   # full 模式
codev-platform health --all 2>/dev/null || true   # 全平台各项目 x 三库 概览(/ai-health skill 同源)
```

在 Claude Code 会话里搜一条:三套 MCP 自动可用，`search_docs(query=...)` 应能从 §3 索引的本仓文档命中结果。MCP 选型见 `.claude/rules/ai-tools-mcp.md`。

---

## 6. (可选) agent 问答 + playground (~5 min)

只读代码理解的 HTTP agent 服务:

```bash
codev-platform agent serve              # 默认本地 :8848
```

> **前提**:agent serve 需 `fastapi` / `uvicorn`(平台 venv 暂未默认装)。缺则先 `pip install fastapi uvicorn`。
> Web playground(浏览器问答 UI)是规划中的交付项,见 `docs/plans/roadmap-2026-06-01/pluginized-fullstack-ai-platform-2026-06-01.md`;落地后从此处提供入口。

---

## 30 分钟验收清单

- [ ] `codev-platform --version` 有输出（CLI 在 PATH）
- [ ] `codev-platform config show` 显示本机正确的模型路径 / device / data_dir
- [ ] `python -m codev_platform.chroma.indexer --force` 成功建出本仓文档 collection
- [ ] `codev-platform serve-mcp status` 端点至少 cross-link / codegraph 为 OK
- [ ] `codev-platform daemon status` 显示 chroma daemon 已加载项目 + chunks > 0
- [ ] `codev-platform health --mode light` banner 为 READY 或 ATTENTION（非 BROKEN）
- [ ] Claude Code 会话里 `search_docs` 能命中本仓文档

---

## 相关文档

| 文档 | 内容 |
|---|---|
| [SETUP.md](./SETUP.md) | 换机器 / 服务器完整安装 onboarding（含 CUDA 轮子顺序） |
| [USAGE.md](./USAGE.md) | 装好之后的日常子命令手册 |
| `.claude/rules/ai-tools-mcp.md` | 三套 MCP 触发指南 + 故障应急 |
| `config.example.json` | 用户级 config 字段全集 + 显卡参数说明 |
