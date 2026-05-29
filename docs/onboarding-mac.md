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

## 接入 Claude Code(关键一步:用 launchctl,不要用 ~/.zshrc)

`.mcp.json` 是 Win/Mac 共用的提交文件,用 `${VAR:-默认}` 展开:**变量没设时 = Windows 行为**(`cmd /c`)。Mac 要让 VSCode 扩展宿主在**展开阶段**就看到这 4 个变量,才会切到 stdio 直跑 server。

> ⚠️ **别放 `~/.zshrc`**。从 Dock / 聚焦 / 双击图标启动的 VSCode **不读 `~/.zshrc`**(只继承 launchd 环境),扩展宿主拿不到变量 → `.mcp.json` 回退成 Windows 的 `cmd` 命令 → Mac 上必然 `failed`。必须用 **`launchctl`**(进 launchd 环境,GUI 启动也可见),并用 **LaunchAgent** 让它重启电脑不丢。

把下面**整段**粘到终端跑一次(写 LaunchAgent + 立即生效;`$CLAUDE_PROJECT_DIR` 留字面,运行时由 Claude Code 注入):

```bash
PLIST=~/Library/LaunchAgents/com.codev-platform.mcp-env.plist
mkdir -p ~/Library/LaunchAgents
cat > "$PLIST" <<'PLIST_EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.codev-platform.mcp-env</string>
  <key>RunAtLoad</key><true/>
  <key>ProgramArguments</key><array>
    <string>/bin/sh</string><string>-c</string>
    <string>launchctl setenv PLATFORM_MCP_SH sh; launchctl setenv PLATFORM_MCP_FLAG -c; launchctl setenv PLATFORM_MCP_CHROMA 'exec "$CLAUDE_PROJECT_DIR/.venv/bin/python" -m codev_platform.chroma.server'; launchctl setenv PLATFORM_MCP_CROSSLINK 'exec "$CLAUDE_PROJECT_DIR/.venv/bin/python" -m codev_platform.cross_link.server'; launchctl setenv PLATFORM_CODEGRAPH /usr/local/bin/codegraph</string>
  </array>
</dict></plist>
PLIST_EOF
launchctl unload "$PLIST" 2>/dev/null; launchctl load "$PLIST"
# 立即生效一份(本次会话,不必等下次登录)
launchctl setenv PLATFORM_MCP_SH sh
launchctl setenv PLATFORM_MCP_FLAG -c
launchctl setenv PLATFORM_MCP_CHROMA 'exec "$CLAUDE_PROJECT_DIR/.venv/bin/python" -m codev_platform.chroma.server'
launchctl setenv PLATFORM_MCP_CROSSLINK 'exec "$CLAUDE_PROJECT_DIR/.venv/bin/python" -m codev_platform.cross_link.server'
launchctl setenv PLATFORM_CODEGRAPH /usr/local/bin/codegraph   # 仅装了 codegraph 才需要; 路径以 `which codegraph` 为准
```

> 前 4 个值对每个 Mac 同学**完全一样**(走 `$CLAUDE_PROJECT_DIR/.venv` 项目相对路径,跟克隆位置/用户名无关),直接抄。`PLATFORM_CODEGRAPH` 是 codegraph 二进制的**绝对路径**(Dock 启动的 VSCode 的 PATH 不含 `/usr/local/bin`,故 `.mcp.json` 里 codegraph 也走 `${PLATFORM_CODEGRAPH:-codegraph}`)。卸载:`launchctl unload "$PLIST" && rm "$PLIST"`。

然后 **完全退出 VSCode(Cmd+Q)再从 Dock 重开** —— 新的扩展宿主才会从 launchd 读到这些变量。

> 快速替代(不想装 LaunchAgent):从**终端**里 `code .` 启动 VSCode,可继承终端的 shell 环境;但每次都得从终端开,易忘。

---

## (可选)codegraph 代码图谱 MCP

`.mcp.json` 里还有第三个 server `codegraph`(**独立第三方工具**,通用代码图谱:符号 / 调用链 / impact)。想要就装:

```bash
npm install -g @colbymchenry/codegraph   # 第三方个人作者包, 自行评估信任; 提供 /usr/local/bin/codegraph
which codegraph                          # 确认路径(上面 LaunchAgent 的 PLATFORM_CODEGRAPH 要对上)
cd <codev-platform 仓> && codegraph init -i   # 建本仓 .codegraph/codegraph.db(gitignored, 每台各建)
```

> PATH 坑同 env:Dock 启动的 VSCode PATH 不含 `/usr/local/bin`,所以 `.mcp.json` 用 `${PLATFORM_CODEGRAPH:-codegraph}`,靠上面 LaunchAgent 里的 `PLATFORM_CODEGRAPH` 绝对路径找到它。不装 codegraph 就忽略,另外两个 server 不受影响。

---

## 验证

1. Claude Code 里 `/mcp` → `platform-docs`、`cross-link` 应为 **connected**(装了 codegraph 则三个全 connected)。
2. 让 Claude 调一次 `search_docs`(如查"commit 规范"),应返回相关文档 chunk。
3. `cross-link` connected,但其工具可能报"DB 不存在" —— 正常,cross-link 数据本流程没建(只覆盖 chroma 检索)。

---

## 排错

| 现象 | 原因 / 处理 |
|---|---|
| `/mcp` 里 platform-docs failed | 4 个 env 没进 launchd → `launchctl getenv PLATFORM_MCP_CHROMA` 应非空;为空就重跑上面那段 + **Cmd+Q 完全重启** VSCode(reload window 不够) |
| daemon 日志 `模型加载失败` | `~/models/Qwen3-Embedding-0.6B` 没下全,或 config `embed_path` 写错 |
| mps 报算子不支持 | config `embed_device` 改 `cpu`(0.6B 模型 CPU 也够快) |
| search 返回空 | 索引没建 → 跑 `python -m codev_platform.chroma.indexer --force` |
| `/mcp` 里 codegraph failed | ① `which codegraph` 为空 → 没装(`npm i -g @colbymchenry/codegraph`);② 装了但 `launchctl getenv PLATFORM_CODEGRAPH` 为空/路径不对 → 重设 + Cmd+Q 重启;③ 没建库 → `codegraph init -i` |
