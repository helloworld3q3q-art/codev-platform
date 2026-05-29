# 接入新项目到 codev-platform(Mac)

> 把**任意一个你自己的项目**接进 codev-platform 的三套 AI 检索 MCP(代码图谱 / 文档检索 / 全栈链路)。
> 前提:codev-platform 本体已在本机装好(见 [onboarding-mac.md](onboarding-mac.md)):`<CODEV>/.venv` 装了 `[runtime]`、`~/models/Qwen3-Embedding-0.6B` 在、`codegraph` 已全局装(`/usr/local/bin/codegraph`)。
> 记号:`<CODEV>` = codev-platform 仓绝对路径(本机 `/Users/huangyuchuan/Documents/other/codev-platform`)。

---

## 模型(30 秒)

codev-platform 是**装一次、多项目共用**的工具栈,**按 `project_id` 多租户隔离**:

- 每个项目仓根放 `.claude/project.json`(一个 `project_id`)。
- Claude Code 打开哪个仓,server 就按那个仓的 `project_id` + 该仓 `data/` 提供检索 —— 各项目互不串。
- 三套 server 复用 `<CODEV>/.venv`(不用每个项目重装几个 GB torch)。

> **为什么 `.mcp.json` 里写绝对路径、不绕环境变量**:Mac 上"靠 launchctl 注入 env 给 VSCode"这条链很脆 —— GUI 启动的 VSCode 不读 `~/.zshrc`,且 VSCode app 进程会**缓存启动时的 env、之后改 launchctl 不刷新**(实测踩过)。所以新项目直接在 `.mcp.json` 写死命令最稳,Claude Code 一读就生效。

---

## 接入步骤

### 1. project_id(自动生成 `.claude/project.json`,不用手写)
```bash
cd <你的项目仓>
<CODEV>/.venv/bin/codev-platform init <project-id> --display-name "<Name>"   # project-id: 小写字母/数字/连字符
```

### 2. `.mcp.json` —— 直写命令(把 `<CODEV>` 换成你的绝对路径)
仓里**没有** `.mcp.json` → 新建下面这份;**已有**(脚手架常预置占位)→ 把 3 个 server 的 `command/args` 填成下面这样(键名沿用你仓已有的,如 `code-graph`/`doc-search`/`business-link` 均可):

```json
{
  "mcpServers": {
    "code-graph": {
      "command": "/usr/local/bin/codegraph",
      "args": ["serve", "--mcp"]
    },
    "doc-search": {
      "command": "sh",
      "args": ["-c", "cd \"$CLAUDE_PROJECT_DIR\" && exec \"<CODEV>/.venv/bin/python\" -m codev_platform.chroma.server"],
      "timeout": 90000
    },
    "business-link": {
      "command": "sh",
      "args": ["-c", "cd \"$CLAUDE_PROJECT_DIR\" && exec \"<CODEV>/.venv/bin/python\" -m codev_platform.cross_link.server"],
      "timeout": 30000
    }
  }
}
```
> `$CLAUDE_PROJECT_DIR` 由 Claude Code 注入、sh 运行时展开 —— 它保证 server 在**你打开的仓根**解析 project_id + 数据,不靠 spawn 时的 cwd(那个不可靠)。
>
> ⚠️ **`business-link` 默认别放**。cross-link 只适用 **Java+Flyway SQL+Python 全栈**(扫接口↔表↔写库),且扫描器在那类业务仓里。TS / 前端 / 纯文档项目放了它,一调就报 `cross_layer.sqlite 不存在`。只在确为该类全栈项目时才加这第三个 server。

### 3. doc_patterns —— 文档不在默认位置时必做
默认只扫 `CLAUDE.md` / `README.md` / `docs/**`。**设计文档在根目录或别处**(如 `PRD.md`、`prompts/*.md`)就建 `<你的仓>/.claude/index.json`:
```json
{ "doc_patterns": ["*.md", "docs/**/*.md", "prompts/*.md", ".claude/rules/*.md"] }
```
不建的话 chroma 只索引到极少文件。

### 4. 建索引(⚠️ 用 `reindex`,别裸跑 indexer)
```bash
cd <你的项目仓>
<CODEV>/.venv/bin/codev-platform reindex --chroma --force   # 文档 → 本仓 data/chroma(collection <project-id>__platform_docs)
codegraph init -i                                          # 代码图谱 → 本仓 .codegraph/
```
> 别用 `python -m codev_platform.chroma.indexer`:它 `PLATFORM_ROOT` 默认指向 codev-platform 包所在地,会去索引 codev 自己。`reindex` 会把 `PLATFORM_ROOT` 设成你的仓。

### 5. gitignore
```bash
echo "data/chroma/" >> .gitignore   # chroma 索引机器本地, 别提交(.codegraph 的 db 它自带 .gitignore 忽略)
```

### 6. 验证
让 Claude Code 重读 `.mcp.json`:**Cmd+Shift+P → "Developer: Reload Window"**(或 Cmd+Q 重开)→ `/mcp` 三个 server 应 **connected**,检索的是你这个项目的内容。

---

## 说明 / 边界

- **数据隔离**:`~/.codev-platform/config.json` 的 `data.platform_data_dir=null` 时,每个项目索引存各自仓 `data/`(gitignored),互不影响。
- **cross-link(business-link)是进阶项,默认不放**:需 Flyway + Java mapper/controller(+前端/Python)全栈结构,扫描器在那类业务仓里,且要在 `platform_meta/projects/<id>/meta.json` 配 `health.*_patterns` 后单独建图。**非该类项目放了它,调用即报 `cross_layer.sqlite 不存在`** —— 直接从 .mcp.json 删掉。
- **日常维护**:改完重建 —— 在仓里 `codev-platform reindex --chroma --force` / `codegraph sync`;装了 `codev-platform install-hooks` 则 commit 自动重建。

---

## 排错

| 现象 | 处理 |
|---|---|
| `/mcp` doc-search/business-link failed | `.mcp.json` 里 `<CODEV>` 是否换成真实绝对路径;`<CODEV>/.venv/bin/python` 是否存在;Reload Window |
| `current` 报无法解析 project_id | 没跑步骤 1,或不在仓根 → `codev-platform init <id>` |
| search 返回空 | 该仓没建索引 / doc_patterns 没覆盖 → 看步骤 3、4 |
| code-graph failed | `which codegraph` 空就装(`npm i -g @colbymchenry/codegraph`) |
