# Daily Summary — 2026-06-05(W3 loop-guard 重构落地 + 端到端验证)

> 范围:三窗口并行(W1 图谱 / W2 memory / W3 loop-guard)中的 **W3** 全程。
> 关联:`agent-loop-guard-redesign-2026-06-05.md`(plan)、`agent-provider-architecture.md §1/§4`(铁律)。
> commit:`e3c6594`(W3 核心)+ `9246664`(审计 follow-up),均已 push 到 `fuwuqi/dev`。

---

## 一、交付:loop guard 从「全局按工具计次」升级为「按工具语义三分类施策」

`per_tool_cap=3` 把「读 3 个不同文件」误判成空转拦死(aa.txt 实测 agent 明说「因工具调用限制未读取到」),而真正该防的「弱模型换词 thrash」单一 cap 又防不住。重构按工具语义分三类:

- **只读类**(`read_file`/`list_dir`):归一化 distinct-path 上限(高,默认 20)+ 总读软顶(默认 30),同 path 忽略 offset/limit 判零增量。读不同文件 = 确定进展,近乎不限。
- **检索类**(`codegraph_*`/`search_docs`/`impact_*`):distinct-args 上限(`retrieval_distinct_cap`)+ **输出侧零增量**(归一化结果哈希,连续 `no_progress_limit` 次无新增 → 软提示→硬拒→强制收尾)。
- **无效调用类**(参数报错/路径不存在/非法 module):连续 `invalid_call_limit` 次 → 回灌合法值清单 + 强制换路,不放行无限重试。
- **收尾合规**:near_limit 提示加「禁脑补未读内容」;读取充分性门——distinct 成功读 < 阈值 **且尾部连续无效** → 判「卡无效调用」,区分「空转」与「读够了」。

**模型无关铁律守住**:`loop.py` 零模型名 if-else;模型差异 100% 落 `LoopPolicy` 数值/开关,经 `registry.loop_policy()._pick` 逐字段解析(`agent.providers.<name>.loop.<f>` > `agent.loop.<f>` > spec 档默认 > 全局默认)。能力档矩阵 `_STRONG`(claude/gpt,关 novelty + 宽 cap)/`_MID`(qwen)/`_WEAK`(deepseek),加模型 = 选档一行,核心零改。`per_tool_cap` 保 deprecated 别名映射 `retrieval_distinct_cap`(不破存量 config/测试)。

## 二、P0:放开 `search_docs` 的 module 枚举(chroma/_schema.py)

aa.txt 实测 codev-platform 的 `module="web-ui"` 被 MCP `inputSchema` enum 硬拒(enum 写死 stock-* + platform)。选**方案②**:去掉 `module` 硬 enum → 自由字符串 + 描述引导用 `list_collections` 看本项目模块;`_build_where` 对未知 module 优雅返空不报错。理由:逐项目动态模块集用 schema 静态 enum 是错误机制,放开比加项更彻底(对所有项目一劳永逸)。`category` enum 保留(它是固定集)。

## 三、审计 + 测试(两轮)

- **第一轮**:审计兄弟(无 BLOCKER/MAJOR)+ 测试兄弟(Gate 覆盖核查),据此修 1 个真 MINOR(充分性门加 `consecutive_invalid > 0`,纯检索任务不再被误判卡无效)+ 补 2 个回归测试(P0 schema 无 enum、P2 归一化抗加空格/标点)→ `9246664`。
- **第二轮(完整复审最终态)**:**38 passed / 0 failed**,无 BLOCKER/MAJOR;对抗项(零 if-else / frozen 别名 / `_bool` 三路 / novelty 边界 / 充分性门双向 / 去 enum 无遗漏)全 PASS。残留仅 MINOR(`_bool` 未带 `legacy_keys`,当前无 bool 字段需要别名,不补——避免为未来写代码)。结论:ship as-is。

## 四、端到端验证(WSL 平台)

- WSL 平台仓 `/home/helloworld/work/codev-platform` 已在 `9246664`,P0 代码在位。
- `agent_tool_health.py`:**11/11 工具**端到端健康(codegraph / cross-link / search_docs / read_file / list_dir / remember)。
- **P0 live 确认**:重启 chroma daemon **前**,daemon 仍拒 `module="web-ui"`(`Input validation error: 'web-ui' is not one of [...]`)——**反向坐实** MCP `inputSchema` enum 是**服务端强校验**;重启**后** `module="web-ui"` 与未知 module 均 `rejected=False` 返空,基线 module 正常有结果。
- daemon 重启干净:旧 PID → 新 PID,`serve-mcp start --wait` 三端点 OK,cross-link/codegraph `already-up` 未受影响。

## 五、踩坑 / 教训

1. **多窗口并行禁 `git add -A`**:首个 commit 被 `-A` 卷入 W2 未提交的 10 个文件。未 push 时 `git reset --soft HEAD~1` + `git restore --staged .` 拆出来,W2 改动原样留回工作树(零丢失,后由 W2 窗口自己提交成 `f656c6a`)。沉淀为 memory `multi-window-git-add-scope`。落点:`workflow.md §6.1`。
2. **改 MCP schema 必重启 daemon**:`inputSchema` enum 服务端强校验,daemon 不随 pull/commit 热重载(MEMORY `wsl-mcp-daemons-stale-after-pull`)。`serve-mcp` 无 `restart`,reload = `pkill -f chroma.server` + `serve-mcp start --wait`。
3. **WSL bridge 引号坑**:`wsl.exe bash -c '<payload>'` 会把 payload 再包一层双引号,内含 `"`/`|`/`()` 会被外层 shell 重解析 → 复杂命令写成 `.sh` 文件用 `wsl.exe bash <file>` 跑,免引号穿层。
