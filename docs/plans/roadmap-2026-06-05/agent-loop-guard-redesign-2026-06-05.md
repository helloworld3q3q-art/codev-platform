# Agent Loop Guard 重构 — 工具分类护栏(2026-06-05)

> 状态:📋 待启动(plan)
> 来源:`aa.txt`(2026-06-04 修复后重跑实测)+ 5 专家两轮会诊(loop 控制 / 务实 / 红队 ×2 / 架构)。
> 关联:`.claude/rules/agent-provider-architecture.md §4`(弱模型代码护栏)、`codev_platform/agent/loop.py`、`codev_platform/agent/policy.py`。

---

## 一、定位

把 `agent/loop.py` 的护栏从「**全局按工具计次**(`per_tool_cap`)」升级为「**按工具语义三分类施策**」:

- **只读探索类**(`read_file`/`list_dir`):读不同文件 = 确定进展 → 近乎不限,只设归一化 distinct-path 上限 + 总量软顶。
- **检索类**(`codegraph_*`/`search_docs`):query 换词可绕指纹 → 加**输出侧零增量护栏**(结果哈希 + 连续无进展计数)。
- **无效调用类**(参数报错 / 非法 module / 路径不存在):单独识别,连续 K 次 → 回灌合法值 + 强制换路,不放行无限重试。

**要解决的主矛盾**:修复 search_docs OOM + 新增 fs 工具后,瓶颈从「工具不可用」转移到「护栏过紧」——`per_tool_cap=3` 把「读 3 个不同文件」误判成空转拦死(agent 明说「因工具调用限制未读取到」),而真正该防的「弱模型换词 thrash」用单一 cap 又防不住。

## 二、决策前提

- **实测证据成立**(`aa.txt` 重跑):search_docs 不再 OOM、`read_file`/`list_dir` 有效、答案准确度大幅提升;瓶颈确定为护栏过紧。
- **distinct path = 确定进展**;**query 换词不可信**(弱模型加空格/同义词/调 limit → 无限「新指纹」)→ 工具必须**分类**施策,单一全局 cap 数值无论调大调小都错(大=放行 thrash,小=误杀只读)。
- **不引入语义相似 / embedding 评分**(过度设计 + 阈值难调 + 误判)——只用确定性指纹 + 归一化哈希等值判断。
- **强制收尾能治空转,治不了「没读够就被掐 → 答案空」**(`aa.txt` 里 agent 被迫在信息不全时收尾)→ 需「读取充分性门 + 禁脑补 prompt」兜底,否则护栏越紧幻觉越多。
- 一切阈值进 `LoopPolicy`、按 provider 解析(§4 配置驱动,弱模型严 / 强模型可关),核心循环零 if-else。

## 三、Phase 划分

### P0 — module 枚举修复 + 无效调用防线(est 0.5d)
- 修 `search_docs` 的 `module` 枚举对 codev-platform 失效(`aa.txt` line 413:`module="web-ui"` 被拒,enum 写死 stock-* 项目)。两候选留 design doc 定:① 按当前 project_id 动态枚举该项目已索引模块;② 放开字段 + 服务端忽略未知 module 不报错。
- loop 加「无效调用类」:连续 K 次(默认 2)参数报错(非法 module / 路径不存在)→ 回灌合法值清单 + 强制换路;**不计正常预算、不放行无限重试**(治弱模型在错误参数上空转)。
- **Gate**:`search_docs(module="web-ui")` 不被 schema 拒;构造连续无效调用 → 第 K 次被拦并回灌合法枚举,不无限空转。

### P1 — 工具三分类 + 只读放宽(est 0.5d)
- `READONLY` / `RETRIEVAL` / `WRITE` 三集合(loop 内常量,不配置)。
- 只读类:去掉 `per_tool_cap`,改「**归一化 path** 的 distinct-path 上限(高,如 20)+ **总读次数软顶**(25–30,防乱读无关文件刷)」;同 path(忽略 offset/limit)判零增量、由现成 `seen_calls` 拦。
- **Gate**:agent 连读 8 个不同文件不被拦;同文件改 offset 刷读被判零增量;乱读触总量软顶;`tests/test_agent_loop_guard.py` 现有用例不回归。

### P2 — 检索类 distinct-args + 输出侧零增量(est 1d)
- `per_tool_cap` → `retrieval_distinct_cap`(只计 distinct-args,复用现成指纹,默认 8)。
- 检索结果**归一化哈希**(裁空白 / 取 top-N 结果 id 集合而非原文)+ 连续零增量计数 `no_progress_limit`(默认 3)→ 软提示 → 硬拒 → 强制收尾。
- **结果哈希仅 `RETRIEVAL` 类**(read_file 读不同文件天然 novel,套上去等于不设防 + 空文件/同名 `__init__` 撞哈希误拦)。
- **Gate**:同义换词但结果相同 → 连续 3 次后强制收尾;正常换查法(结果不同)放行;归一化能挡「加空格/标点」绕过。

### P3 — 配置接线 + 收尾合规(est 0.5d)
- `LoopPolicy` 加字段(`readonly_distinct_cap` / `readonly_total_cap` / `retrieval_distinct_cap` / `no_progress_limit` / `novelty_check`),`per_tool_cap` 保 **deprecated 别名**映射 `retrieval_distinct_cap`(不破 deepseek=3 现有 config + 测试)。
- 收尾 prompt 补:① **禁脑补未读内容**;② **读取充分性门** —— distinct-path < 阈值(默认 2)时收尾文案改为「几乎没读到文件,疑似卡在无效调用,检查参数后重试一次再收尾」,区分「空转」与「读够了」。
- **Gate**:`test_agent_registry` 覆盖新字段 config 覆盖 + 别名向后兼容;deepseek 旧 `per_tool_cap=3` 仍生效。

## 四、风险

- **归一化哈希太松**→正常进展误判零增量;**太紧**→空文件/同名文件撞哈希误拦(缓解:仅 RETRIEVAL 类 + 取结果 id 集合而非原文;`novelty_check` 可关退回现状)。
- **只读总量软顶设太高**→失控读吃爆 context(缓解:总顶 25–30 + 单文件 60KB 上限已在 `fs.py`)。
- **read-only path 不归一化**→offset 刷读绕过(P1 Gate 显式断言同 path 不同 offset 判零增量)。
- **deprecated 别名遗漏**→存量 config 失效(P3 Gate 断言)。
- **读取充分性门阈值难定**→过早掐 vs 纵容(缓解:阈值进 config,先保守 = 2,实测再调)。

## 五、替代方案

- **A. 只升全局 `per_tool_cap` 数值** —— 否:不解决换词绕过,且仍误杀只读。
- **B. 纯 distinct-args、无输出侧** —— 红队否:弱模型加空格/同义词无限造新指纹,空转照旧。
- **C. 语义新颖度评分 / embedding 去重** —— 务实派否:过度设计 + 引入误判 + 依赖。
- **选定 = 工具三分类 + 确定性输出哈希 + 无效调用防线**,三方折中:输入侧粗筛(指纹归一化)+ 输出侧承重墙(结果哈希/零增量,仅检索类)+ 无关兜底(`max_steps`)+ 只读专属(distinct-path/总量软顶)。

## 六、启动条件

- agent venv 可跑 `tests/test_agent_loop_guard.py`(纯单元,**无需 daemon / GPU**)。
- P0 需先在 design doc 定位 `search_docs` 的 `module` enum 真值源(疑在 chroma schema)并定两候选取舍。
- 重 GPU/daemon 实测非必须(护栏逻辑单元测试可全覆盖),但建议改完用 `tools/dev/agent_tool_health.py` + 浏览器重跑 `aa.txt` 同款问题做端到端回归。
