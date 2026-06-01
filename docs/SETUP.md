# codev-platform 跨平台 onboarding (Windows / macOS / Linux)

> 想最快跑通全链路看 [QUICKSTART.md](./QUICKSTART.md)（30 分钟:install → config → index → serve → test query）。本文是完整安装细节。
>
> 目标:换机器 / 队友 / 服务器一把装起来。路径全走 `~/.codev-platform/config.json`,代码零硬编码盘符。
> 平台所有权翻正 (2026-05-28) 后:venv + data + 模型全归 codev-platform 仓自有
> (`.venv` / `data/`),daemon 直接从本仓 `.venv` 跑。

---

## 0. 前置 (一次性)

| 工具 | 安装 |
|---|---|
| Python 3.11+ | python.org / pyenv / brew |
| `uv` | Windows: `irm https://astral.sh/uv/install.ps1 \| iex`<br>macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| `claude` (Claude Code CLI) | Anthropic 官方 |
| 模型 (自备,不下载) | 见 §4 |

`codev-platform setup` 的 preflight (step 0) 会自动检查 `uv` / `claude` 是否在 PATH。

---

## 1. 三仓 clone 到同一父目录

`.mcp.json` 用 `..\platform` 相对路径,三仓**必须**在同一父目录下:

```
<parent>/
  platform/                 # 业务仓 (openclaw-stock)
  codev-platform/           # 工具栈本体 (本仓, venv/data/模型 都在这)
  codev-platform-widget/    # Tray UI (可选)
```

```bash
cd <parent>
git clone <platform>.git
git clone <codev-platform>.git
git clone <codev-platform-widget>.git   # 可选
```

---

## 2. 建 venv + 装运行时依赖 (在 codev-platform 仓)

venv 目标 = `codev-platform/.venv`(本仓自有,跨平台 `Scripts/python.exe` (win) / `bin/python` (posix))。

### Windows (CUDA GPU)

```powershell
cd <parent>\codev-platform
python -m venv .venv
.venv\Scripts\activate
# torch 必须先单独装 CUDA 版 (cu128 对应 RTX 5060 Blackwell + CUDA 12.8),
# 否则 pip 拉 CPU 版,模型推理慢 10×。具体版本见 requirements-runtime.txt 顶部注释。
pip install torch==2.11.0+cu128 --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements-runtime.txt
pip install -e .
```

### macOS / Linux (无 NVIDIA GPU,纯 CPU / MPS)

```bash
cd <parent>/codev-platform
python -m venv .venv
source .venv/bin/activate
# 不要装 +cu128 轮子(macOS/无 N 卡机器没有 CUDA)。装默认 torch 即可,
# 然后在 config 把 models.embed_device / reranker_device 设成 cpu(mac 可试 mps)。
pip install torch                       # PyPI 默认轮子 (CPU / mac MPS)
pip install -r requirements-runtime.txt # 此文件含 --extra-index-url cu128,
                                        # 但 torch 已 pin 装好,会被 already-satisfied 跳过
pip install -e .
```

> 说明:`requirements-runtime.txt` 顶部带 `--extra-index-url .../cu128` + `torch==2.11.0+cu128`。
> 非 N 卡机器先手动 `pip install torch`(默认轮子),再跑 `-r`,torch 行会被 "already satisfied" 跳过;
> 或自行注释掉 torch pin 行后再跑(别改 cu128 给 N 卡用的那条注释)。

---

## 3. 一键接入:`codev-platform setup`

```bash
codev-platform setup --dry-run    # 先看探测结果 (venv / 模型 / config 走哪条路径), 不写
codev-platform setup --auto       # 真装: 缺 venv 自动 uv venv + pip install -r/-e, sync rules+skills
```

`setup` 会:
1. preflight 检查 `uv` / `claude`
2. 校验三仓同父目录
3. 探测 / (--auto) 创建本仓 `.venv` + editable 装 `codev_platform`
4. 探测模型 (优先 `config.models.embed_path` → `~/models/Qwen3-Embedding-0.6B` → 仓内 MiniLM fallback)
5. 写 `~/.codev-platform/config.json`:
   - `runtime.chroma_venv = <codev-platform>/.venv`
   - `data.platform_data_dir = <codev-platform>/data`
   - `models.embed_path` / `models.reranker_path`
6. (--auto) sync rules + skills 到三仓 `.claude/`

**路径全写进 config.json,代码无任何盘符硬编码。** 换机器只改 config,代码不动。

---

## 4. 模型 (自备,setup 不下载)

| 模型 | 放哪 | 缺失影响 |
|---|---|---|
| Qwen3-Embedding-0.6B | `~/models/Qwen3-Embedding-0.6B` (或 config.models.embed_path 指定) | 无 embed → 仓内 MiniLM fallback 自动用 |
| Qwen3-Reranker-0.6B | `~/models/Qwen3-Reranker-0.6B` (或 config.models.reranker_path) | 可选;缺则 daemon 降级纯向量召回 |

非 GPU 机器记得 config 里把 `models.embed_device` / `models.reranker_device` 改 `cpu`。

---

## 5. 装 git hooks (各业务仓)

post-commit reindex / pre-push audit hook 是 per-repo 的,每个要用的业务仓各跑一次:

```bash
cd <parent>/platform
codev-platform install-hooks      # 或 codev-platform-widget / codev-platform 自身
```

---

## 6. 自测

```bash
codev-platform current            # 当前 cwd 解析到的 project_id
codev-platform list-projects
codev-platform config show        # 看 config.json 实际路径 (确认 venv/data/模型)
```

任一仓打开 Claude Code 即可,daemon 第一次会话从本仓 `.venv` 自动 spawn。
