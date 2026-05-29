# 接入新项目到 codev-platform(Mac)

> 把**任意一个你自己的项目**接进 codev-platform 的三套 AI 检索 MCP(platform-docs / cross-link / codegraph)。
> 前提:codev-platform 本体已在本机装好(见 [onboarding-mac.md](onboarding-mac.md))。
> 记号:`<CODEV>` = codev-platform 仓的绝对路径(本机:`/Users/huangyuchuan/Documents/other/codev-platform`)。

---

## 模型(先理解,30 秒)

codev-platform 是**装一次、多项目共用**的工具栈,**多租户按 `project_id` 路由**:

- 每个项目在自己仓根放一个 `.claude/project.json`(里面一个 `project_id`)。
- Claude Code 打开哪个仓,MCP server 就按那个仓的 `project_id` + 该仓的 `data/` 提供检索 —— **各项目数据天然隔离,互不串**。
- 三套 server 复用 codev-platform 的同一个 `.venv`(不用每个项目重装几个 GB 的 torch)。

---

## 一次性设置(只做一次,之后所有项目复用)

让 MCP 走 **codev-platform 的共享 venv**(否则每个项目得在自己 `.venv` 里装一遍 runtime)。把 launchctl 的 `PLATFORM_MCP_*` 指向 `<CODEV>/.venv` 绝对路径,并写进 LaunchAgent 持久化:

```bash
CODEV=/Users/huangyuchuan/Documents/other/codev-platform     # ← 改成你的 codev-platform 路径
PLIST=~/Library/LaunchAgents/com.codev-platform.mcp-env.plist

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.codev-platform.mcp-env</string>
  <key>RunAtLoad</key><true/>
  <key>ProgramArguments</key><array>
    <string>/bin/sh</string><string>-c</string>
    <string>launchctl setenv PLATFORM_MCP_SH sh; launchctl setenv PLATFORM_MCP_FLAG -c; launchctl setenv PLATFORM_MCP_CHROMA 'exec \"$CODEV/.venv/bin/python\" -m codev_platform.chroma.server'; launchctl setenv PLATFORM_MCP_CROSSLINK 'exec \"$CODEV/.venv/bin/python\" -m codev_platform.cross_link.server'; launchctl setenv PLATFORM_CODEGRAPH /usr/local/bin/codegraph</string>
  </array>
</dict></plist>
PLIST_EOF

launchctl unload "$PLIST" 2>/dev/null; launchctl load "$PLIST"
# 立即生效(本次会话,不必等下次登录)
launchctl setenv PLATFORM_MCP_CHROMA "exec \"$CODEV/.venv/bin/python\" -m codev_platform.chroma.server"
launchctl setenv PLATFORM_MCP_CROSSLINK "exec \"$CODEV/.venv/bin/python\" -m codev_platform.cross_link.server"
launchctl setenv PLATFORM_MCP_SH sh; launchctl setenv PLATFORM_MCP_FLAG -c; launchctl setenv PLATFORM_CODEGRAPH /usr/local/bin/codegraph
```

> 区别:之前是 `$CLAUDE_PROJECT_DIR/.venv`(= 各项目自己的 venv)→ 现在固定 `$CODEV/.venv`(共享)。server 仍按你打开的仓解析 `project_id`/数据,路由不变。codev-platform 自己仓照常工作。

---

## 每个新项目

> `CODEV=/Users/huangyuchuan/Documents/other/codev-platform`

### 1. project_id(自动生成 `.claude/project.json`,不用手写)
```bash
cd <你的项目仓>
$CODEV/.venv/bin/codev-platform init <project-id> --display-name "<Name>"   # 例: abra(小写字母/数字/连字符)
```

### 2. `.mcp.json` —— 看你仓里有没有
- **没有** → 直接拷:`cp $CODEV/.mcp.json .mcp.json`
- **已有**(很多脚手架会预置占位)→ **不要覆盖**,把这 3 个 server 的 `command/args` 填成 codev 的(键名可沿用你仓已有的,如 `code-graph`/`doc-search`/`business-link`):
  - codegraph: `"command": "${PLATFORM_CODEGRAPH:-codegraph}", "args": ["serve","--mcp"]`
  - 文档检索: `"command": "${PLATFORM_MCP_SH:-cmd}", "args": ["${PLATFORM_MCP_FLAG:-/c}", "${PLATFORM_MCP_CHROMA:-...}"]`
  - 链路: 同上,末参换 `${PLATFORM_MCP_CROSSLINK:-...}`

### 3. doc_patterns —— 你的文档不在默认位置时必做
默认只扫 `CLAUDE.md` / `README.md` / `docs/**`。**如果你的设计文档在根目录或别处**(如 `PRD.md`、`prompts/*.md`),建 `<你的仓>/.claude/index.json` 指定:
```json
{ "doc_patterns": ["*.md", "docs/**/*.md", "prompts/*.md", ".claude/rules/*.md"] }
```
不建的话,chroma 只会索引到极少文件。

### 4. 建索引(⚠️ 用 `reindex`,不要裸跑 indexer)
```bash
cd <你的项目仓>
$CODEV/.venv/bin/codev-platform reindex --chroma --force   # 文档 → 你仓的 data/chroma(collection <project-id>__platform_docs)
codegraph init -i                                          # 代码图谱 → 你仓的 .codegraph/
```
> 为什么不用 `python -m codev_platform.chroma.indexer`:裸跑它的 `PLATFORM_ROOT` 默认指向 **codev-platform 包所在地**,会去索引 codev-platform 自己、而非你的仓。`reindex` 会自动把 `PLATFORM_ROOT` 设成你的仓。

### 5. gitignore + (可选)规则同步
```bash
echo "data/chroma/" >> .gitignore          # chroma 索引是机器本地, 别提交(.codegraph 的 db 它自带 .gitignore 忽略)
# 可选: 同步 codev 通用规则/skill(若你仓没有自己的一套)
$CODEV/.venv/bin/codev-platform sync-rules && $CODEV/.venv/bin/codev-platform sync-skills
```

### 6. 验证
**完全退出 VSCode(Cmd+Q)再打开你的项目** → `/mcp` 三个 server connected,检索的是**你这个项目**的内容。

---

## 验证

- `/mcp` → platform-docs、codegraph **connected**(cross-link 见下)。
- 让 Claude `search_docs` 查你项目里某个规则/文档 → 返回你项目的 chunk。
- `$CODEV/.venv/bin/codev-platform current`(在你仓根跑)应打印你的 `project_id`。

---

## 说明 / 边界

- **数据隔离**:`config.json` 的 `data.platform_data_dir` 为 `null` 时,每个项目索引存进**各自仓的 `data/`**(gitignored),互不影响。想集中存一处再按 `project_id` 前缀隔离,才需设 `platform_data_dir`。
- **cross-link(全栈链路)是进阶项**:它要项目是 Flyway + Java mapper/controller + (可选)Python/前端 的全栈结构,且要在 `platform_meta/projects/<id>/meta.json` 配 `health.*_patterns` 后单独建图。纯文档/单语言项目用不到,留它 connected 但空即可。
- **codegraph 是第三方工具**:需先全局装过(`npm i -g @colbymchenry/codegraph`,见 onboarding-mac.md),每个项目 `codegraph init -i` 各建一次库。
- **日常维护**:改了代码/文档重建索引 —— `python -m codev_platform.chroma.indexer --force` / `codegraph sync`;装了 `codev-platform install-hooks` 则 commit 时自动重建。

---

## 排错

| 现象 | 处理 |
|---|---|
| `/mcp` platform-docs failed | `launchctl getenv PLATFORM_MCP_CHROMA` 应非空且指向 `$CODEV/.venv`;空就重跑「一次性设置」+ Cmd+Q 重启 |
| `current` 报无法解析 project_id | 没跑第 1 步,或不在仓根 → `codev-platform init <id>` |
| search 返回空 | 该仓没建索引 → 在该仓跑 `indexer --force` |
| codegraph failed | `which codegraph` 空就装;装了核 `PLATFORM_CODEGRAPH` 路径 |
