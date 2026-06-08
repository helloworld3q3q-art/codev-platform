# daily-summary 2026-06-08 —— Phase 7 planner 上线 + 真机 A/B + 硬封顶优化

> 承 [`README.md`](README.md) 落地状态。本日把 Phase 7 Query Planner 从"代码 + 默认关"推到
> **活的 WSL deepseek agent 上线 + 真机量化 + 按数据优化**。

## 一、上线(全链)

`c4030d2` commit → push 内部 Gitea → WSL pull → config 开 `agent.planner.enabled=true`
→ `sudo systemctl restart codev-agent`。live trace(`data/agent_trace/2026-06-08.jsonl`)
确认 `plan` 事件 4/4 query_type 分类全对、lane/预算按类注入:

| query_type | tool_budget | preferred_lanes |
|---|---|---|
| overview | 5 | list_dir, search_docs, read_file |
| impact | 12 | impact_analysis, table_usage, api_callers, page_dependencies, codegraph_callers |
| symbol | 6 | codegraph_search, codegraph_callers, codegraph_callees, read_file |
| doc_rule | 4 | search_docs, read_file |

## 二、真机 A/B(deepseek,工具调用数)

| 类型 | live web-ui (ON) | 受控 A/B ON | 受控 A/B OFF | 预算 |
|---|---|---|---|---|
| overview | 8 | 5 | **16** | 5 |
| impact | 6 | 11 | 7 | 12 |
| symbol | 4 | 3 | 3 | 6 |
| doc_rule | 2 | 4 | 3 | 4 |

**诚实结论**:
1. 部署真成:live 4/4 分类对、答案扎实带 `file:line`,无质量退化。
2. ROI 集中在**防探索型失控**(overview):无 planner 时 deepseek 连刷 16 次(12 次冗余
   `list_dir`),现有 loop guard 的 `readonly_total_cap=30` 根本没拦住 —— 是 planner 预算
   压到 5。impact/symbol/doc_rule 基本中性(偶尔多调换更全答案)。
3. **软预算是真"软"**:live overview 跑了 8 > 预算 5,deepseek 越界继续刷 `list_dir`。
4. 单跑数字有噪声(rule/skill pack 进 prompt + LLM 随机性),方向性而非统计结论。

## 三、优化:超预算后只读类硬拦(`loop.py`)

软预算对 deepseek 不够 → `_precheck` 加:**planner 开 + `executed_tools >= tool_budget` +
只读类(list_dir/read_file)→ 硬拦**(检索类仍走软提示,因其有 distinct/零增量上限自限)。
精准打 overview/general 的目录 spelunking 失控,不碰 impact/symbol(走检索类)。

**真机复验**(同 overview):实际**只读执行从 8 → 封顶 5**(后续 3 次 deepseek 仍提议 `list_dir`
被廉价拒,返回 guard 字符串而非目录 dump → context 更干净),仍 `answered`。

测试:`test_readonly_hard_capped_at_budget_when_planner_on`(执行==5)+
`test_readonly_not_capped_when_planner_off`(原行为不变)。25 passed,现有 loop guard 回归通过。

## 四 b、planner 归位为"每模型策略"(架构对齐)

用户指出:agent 设计是**模型可插拔 + 每模型策略**(因模型特性不同),planner 不该是全局开关。
原实现把 `planner_enabled` 当独立全局 flag(`agent.planner.enabled` + deps 独立 factory)—— 与
既有 `LoopPolicy`/`ProviderSpec` 每模型档机制并行,违反 agent-provider §1-3。**重构归位**:

- `LoopPolicy` 加 `planner_enabled` / `planner_hard_cap_readonly` 字段(策略对象本体)。
- registry 能力档设默认:`_STRONG`(claude/gpt)关 planner;`_WEAK`/`_MID`(deepseek/qwen)开 + 硬封顶。
- `loop_policy(cfg,name)` 按 provider 解析这两字段(`agent.providers.<name>.loop.planner_*` >
  `agent.loop.*` > 档默认),与其它 loop 字段同机制。
- loop 只读 `self.policy.planner_*`(不判模型);删掉 ChatService/deps 的独立 factory + 全局 `agent.planner` 配置块。

效果:**加新模型时 planner 策略随能力档自动给(或 config 逐字段覆盖),不再配全局一个**。
deepseek 的 planner 现由 `_WEAK` 档自带(不依赖那条全局 config)。测试 `test_planner_is_per_model_strategy`
(claude 关 / deepseek 开+硬封顶 / config 覆盖),60 passed。

## 四、未决 / 取向

- impact 预算 12→8 可再 A/B(当前"多调换更全"未必坏,看取向)。
- planner 完整版(LLM planner + agent 端到端 eval + tool budget 真机统计入 eval)移交下一迭代。
- `weblog.txt`(本目录散 txt,用户手工贴的 web-ui run)—— 数据已并入本日报,可删或留自用。
