# Mac 接入 codev-platform AI 检索(Apple Silicon)

> 面向新加入、用 Mac 的同学。让 chroma 语义检索 daemon 在你的 Mac 上跑起来并接入 Claude Code。
> Windows 同学不看这篇 —— 你那边 `.mcp.json` 默认行为开箱即用(`cmd /c` daemon launcher)。
>
> 前提:Apple Silicon(arm64)。Intel Mac 把下文 `mps` 换成 `cpu` 即可。

---

## 一次性安装(约 10 分钟,模型下载占大头)

```bash
# 1. clone 本仓, 进目录
git clone <codev-platform>.git && cd codev-platform

# 2. 建仓内 venv + 装运行时依赖 (torch/chromadb/sentence-transformers 等, 几个 GB)
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[runtime]"

# 3. 下 embedding 模型到 ~/models (~1.1G; hf CLI 随上一步装上)
hf download Qwen/Qwen3-Embedding-0.6B --local-dir ~/models/Qwen3-Embedding-0.6B

# 4. 写用户级 config (或跑 `codev-platform setup` 自动写, 见下)
codev-platform setup        # 探测 venv/模型 + 写 Mac config(mps)+ 打印第 5 步的 shell 块

# 5. 建本仓索引(data/ 不进 git, 每台机各自建一次)
python -m codev_platform.chroma.indexer --force
```

第 4 步若不用 `setup`,手动建 `~/.codev-platform/config.json`:
```json
{
  "models": { "embed_path": "~/models/Qwen3-Embedding-0.6B", "embed_device": "mps", "reranker_enabled": false },
  "data": { "platform_data_dir": null }
}
```
> `config show` 应显示 `exists=True`。`device` Intel Mac 用 `cpu`。reranker 默认关(没下 reranker 模型;要开再单独下 Qwen3-Reranker-0.6B 并改 `reranker_enabled`)。

---

## 接入 Claude Code(关键一步:shell 环境变量)

`.mcp.json` 是 Win/Mac 共用的提交文件,用 `${VAR:-默认}` 展开:**变量没设时 = Windows 行为**。Mac 要靠 4 个环境变量切到 stdio 直跑 server。把下面这块**原样**粘到 `~/.zshrc` 末尾(单引号别动,`$CLAUDE_PROJECT_DIR` 留字面,由 Claude Code 注入):

```bash
# >>> codev-platform MCP cross-platform >>>
export PLATFORM_MCP_SH=sh
export PLATFORM_MCP_FLAG=-c
export PLATFORM_MCP_CHROMA='exec "$CLAUDE_PROJECT_DIR/.venv/bin/python" -m codev_platform.chroma.server'
export PLATFORM_MCP_CROSSLINK='exec "$CLAUDE_PROJECT_DIR/.venv/bin/python" -m codev_platform.cross_link.server'
# <<< codev-platform MCP cross-platform <<<
```

> 这 4 行对每个 Mac 同学**完全一样**(走 `$CLAUDE_PROJECT_DIR/.venv` 项目相对路径,跟克隆位置/用户名无关),直接抄。

然后 **完全退出 VSCode 再重开**(不是 reload window)—— 扩展进程要重新解析 shell 环境才读得到新变量。

---

## 验证

1. Claude Code 里 `/mcp` → `platform-docs` 应为 **connected**。
2. 让 Claude 调一次 `search_docs`(如查"commit 规范"),应返回相关文档 chunk。
3. `cross-link` 也会 connected,但工具报"DB 不存在" —— 正常,cross-link 数据本流程没建(只覆盖 chroma 检索)。

---

## 排错

| 现象 | 原因 / 处理 |
|---|---|
| `/mcp` 里 platform-docs failed | 4 个 env 没生效 → 确认粘进 `~/.zshrc` 且**完全重启**了 VSCode;终端里 `echo $PLATFORM_MCP_CHROMA` 应非空 |
| daemon 日志 `模型加载失败` | `~/models/Qwen3-Embedding-0.6B` 没下全,或 config `embed_path` 写错 |
| mps 报算子不支持 | config `embed_device` 改 `cpu`(0.6B 模型 CPU 也够快) |
| search 返回空 | 索引没建 → 跑 `python -m codev_platform.chroma.indexer --force` |
