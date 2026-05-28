# codev-platform 使用方法（日常使用手册）

> **安装 / 换机器接入见 [SETUP.md](./SETUP.md)**。本文只讲装好之后**怎么用**。
> 所有命令全平台通用（Windows / macOS / Linux）。装好 `pip install -e .` 后，`codev-platform` 已在 PATH。

---

## 0. 速查

```
codev-platform <子命令> [参数]
codev-platform --version
codev-platform <子命令> -h        # 看真实参数
```

子命令分两类：**项目管理类**（init / register / list-projects / current / validate / sync-rules / sync-skills / config / setup）+ **运维类**（health / reindex / post-commit / dirty-check / install-hooks / wait-for-reindex / daemon）。

---

## 1. 运维类子命令

### `health` — 工具栈体检

20+ 项检查：chroma venv / 模型 / torch+CUDA / chroma collection（按 project_id 隔离）/ 索引新鲜度 / platform-docs daemon `/health` / mcp-proxy / cross_layer KG / codegraph db 完整性 / post-commit hook 漏触发 / 近 7 天 usage 统计。退出码：`0` 全绿 / `2` 仅 WARN / `1` 有 FAIL。

```bash
codev-platform health                 # full 模式，体检当前仓
codev-platform health --mode light    # 跳过重探针（embed load / torch / collection 实查）
codev-platform health --project openclaw-stock   # 审计任意已登记项目
codev-platform health --repo <path>   # 指定仓路径
codev-platform health --json-out      # 额外写 widget 快照到规范位置 platform_meta/health/<pid>.json
codev-platform health --json-out <path>   # 写到指定文件
```

顶部 banner：`>>> READY <<<` 全绿 / `>>> ATTENTION <<<` 仅 WARN / `>>> BROKEN <<<` 有 FAIL。

> `--json-out` 产出 Tray widget 读的结构化快照（schema_version / verdict / checks[]），跨平台与 Windows 完全一致。post-commit 后台 reindex 跑完会自动带 `--json-out` 刷新它，所以**提交代码即见新数据**，无需依赖 widget 自身轮询。

### `reindex` — 刷新本地 AI 索引

三阶段：codegraph sync → chroma reindex → cross-layer KG rebuild。**默认三个全跑**；给任一 flag 则只跑选中的。

```bash
codev-platform reindex                 # 全跑（前台，看进度）
codev-platform reindex --chroma        # 只重建 chroma
codev-platform reindex --codegraph     # 只 codegraph sync
codev-platform reindex --cross-link    # 只重建 cross-link
codev-platform reindex --force         # chroma indexer 传 --force（drop + 全重建）
codev-platform reindex --repo <path>   # 指定仓（默认 git rev-parse 当前仓）
```

> 日常增量由 post-commit hook 后台自动跑，手动 `reindex` 多用于首次建索引 / 怀疑索引坏了全量重建。

### `dirty-check` — 判 MCP 可信度

检查工作树是否有 dirty 文件命中 AI 索引范围（codegraph / cross-link / chroma）。退出码：`0` clean（MCP 可信）/ `1` dirty（建议 grep+Read 兜底）/ `2` 非 git 仓。

```bash
codev-platform dirty-check             # 人类可读报告
codev-platform dirty-check --quiet     # 只返回 exit code
codev-platform dirty-check --json      # 输出 JSON 给 AI / 工具（含 affected 分类 + recommendation）
```

### `post-commit` — git hook 入口

diff 刚提交的 commit，把改动文件按 doc/cross_link/codegraph scope 分类，触发对应 reindex。**永不 fail commit**（异常也 exit 0）。一般不手动跑，由 hook 自动触发。

```bash
codev-platform post-commit             # 默认后台 detached 跑 reindex
codev-platform post-commit --foreground  # 前台跑（调试 / hook 漏触发后补跑）
```

### `install-hooks` — 装跨平台 git hooks

per-repo 安装。写一个纯 LF 的 sh stub（`exec codev-platform post-commit`），去掉旧 `.ps1` 里写死的 Windows powershell.exe 路径。若仓内有 `tools/dev/pre-push-audit.ps1` 则一并装 pre-push。

```bash
cd <某业务仓>
codev-platform install-hooks
codev-platform install-hooks --repo <path>
```

### `wait-for-reindex` — 等后台 reindex 完成

轮询 `tools/chroma/reindex.log` 直到目标 commit 的 reindex 块出现 finished 标记。退出码：`0` 完成（或该 commit 不触及可索引文件）/ `1` 超时 / `2` 无 log。

```bash
codev-platform wait-for-reindex                   # 等 HEAD，默认超时 120s
codev-platform wait-for-reindex --commit <sha>
codev-platform wait-for-reindex --timeout-sec 180
```

### `daemon` — chroma daemon 生命周期

只有 `status` / `stop`，**没有 start**（spawn 由业务仓首次 Claude session 经 launcher 自动完成，避免脱离 project_id 起无主 daemon）。

```bash
codev-platform daemon status    # 查 /health：pid / uptime / rss / 已加载项目 chunks
codev-platform daemon stop      # 按 pid 结束；下次 Claude session 自动重拉
```

---

## 2. 项目管理类子命令

### `setup` — 新机器一键接入

见 [SETUP.md](./SETUP.md)。`codev-platform setup --dry-run` 先探测，`--auto` 真装。

### `init` — 创建 `<cwd>/.claude/project.json`

```bash
codev-platform init                       # 交互输入 project_id
codev-platform init my-project --display-name "My Project"
codev-platform init my-project --force    # 覆盖已有
```

### `register` — 注册当前项目到 platform_meta

写 `platform_meta/projects/<pid>/meta.json`，注册后 `list-projects` 可见。不给 project_id 则读 cwd 的 `.claude/project.json`。

```bash
codev-platform register                   # 读当前仓 project.json
codev-platform register my-project --repo-path . --force
```

### `current` / `list-projects` / `validate`

```bash
codev-platform current                    # 当前 cwd 解析到的 project_id + 来源
codev-platform list-projects              # 别名 ls；列出已注册项目
codev-platform validate my-project        # 校验 project_id 格式
```

### `sync-rules` / `sync-skills` — 同步规则 / skill 到业务仓

把 codev-platform 仓内 `rules/` `skills/`（真值源）拷到 `<cwd>/.claude/`。

```bash
codev-platform sync-rules
codev-platform sync-skills
codev-platform sync-rules --dry-run       # 只列不写
```

### `config` — 用户级配置管理

`~/.codev-platform/config.json`：模型路径 / 数据目录 / venv / daemon 端口等。

```bash
codev-platform config show     # 打印当前 config（含文件路径 + 是否存在）
codev-platform config path     # 只打印配置文件位置
codev-platform config init     # 写默认 config
codev-platform config init --force
```

---

## 3. 日常工作流

| 场景 | 怎么做 |
|---|---|
| **写完代码 commit** | 正常 `git commit`。post-commit hook 自动按 scope 后台 reindex（~30-90s）。需等就 `codev-platform wait-for-reindex` |
| **改代码前判 MCP 能不能信** | `codev-platform dirty-check`：exit 0 = 索引干净，放心用 MCP；exit 1 = 有 dirty 命中索引范围，关键结论改回读真实文件 |
| **怀疑工具栈坏了** | `codev-platform health`（三档：full 全探 / light 跳重探针 / `--project X` 审计他项目）。看 banner READY/ATTENTION/BROKEN |
| **索引明显滞后 / 召回差** | `codev-platform reindex`（全量）或 `--chroma` / `--codegraph` / `--cross-link` 单跑 |
| **daemon 崩了 / 占 GPU** | `codev-platform daemon status` 看状态，`daemon stop` 结束（下次 Claude session 自动重拉） |
| **hook 漏触发了**（health 报 `hook missed?` WARN） | `codev-platform post-commit --foreground` 手动补跑 |
| **新机器 / 新仓接入** | 见 SETUP.md；新业务仓另跑一次 `install-hooks` |

---

## 4. 三套 MCP 在 Claude Code 里怎么用

装好后三套 MCP 在 Claude Code 会话里自动可用（按问题类型选，互补不重复）：

| 问题类型 | MCP | 典型工具 |
|---|---|---|
| 找代码定义 / 调用关系 / 影响面 | **codegraph** | `codegraph_context`（PRIMARY）/ `codegraph_search` / `codegraph_callers` / `codegraph_callees` / `codegraph_impact` |
| 找规则 / 设计文档 / 事故复盘 | **platform-docs** | `search_docs(query, module?, category?)` / `get_by_file` |
| 找前端 API ↔ Java endpoint ↔ Table 业务链路 | **cross-link** | `find_endpoint_link` / `find_table_refs` / `search_nodes` / `cross_link_stats` |

要点：MCP 已预索引，优先于 `grep`+`Read` 循环；但 dirty 命中索引范围 / 刚 commit 60s 内 / 索引滞后时，MCP 结果仅作导航，关键结论回读真实文件。详见 `.claude/rules/ai-tools-mcp.md`。

---

## 5. 跨平台说明

- **CLI 全平台通用** —— `health` / `reindex` / `post-commit` / `dirty-check` / `install-hooks` / `wait-for-reindex` / `daemon` 等子命令在 Windows / macOS / Linux 行为一致，路径全走 `~/.codev-platform/config.json`，代码零盘符硬编码。
- **`.ps1` 只是 Windows 薄 shim** —— `scripts/*.ps1` 现退化为转调 `codev-platform <子命令>` 的薄壳，仅为兼容旧 Windows 调用入口（如双击 / 旧 git hook）。重逻辑已全在 Python `codev_platform.ops`。
- **Mac / Linux 直接敲 `codev-platform`** —— 不需要也不存在对应 `.ps1`；git hook 装的是纯 LF 的 sh stub。

---

## 6. 相关文档

| 文档 | 内容 |
|---|---|
| [SETUP.md](./SETUP.md) | 换机器 / 队友 / 服务器安装 onboarding |
| `.claude/rules/ai-tools-mcp.md` | 三套 MCP 触发指南 + 故障应急 |
| `.claude/rules/workflow.md §3.2` | 任务类型 → MCP 选型映射 |
| `docs/plans/roadmap-2026-05-28/xplatform-cli-2026-05-28.md` | 跨平台 CLI 重构计划 |
| `docs/log/2026-05.md` | 本月平台改造决策日志 |
