# 改动后验证清单 + 测试基线红线

## 1. 按改动层级验证

| 改动层 | 验证命令 |
|---|---|
| Python | `python -m pytest tests/` + `python -m stock_pipeline.config.validate` |
| Java | `mvn compile` + `mvn test` + 重启服务 + `bash test_endpoints.sh` |
| 前端 | `pnpm run lint:fix` + 浏览器手测改动页面 |
| 数据库 | 新迁移须本地起服务验证 Flyway 通过 |
| 跨层 | 三端联调：先重启 Java，再 `pnpm run api` 重新生成接口 |

## 2. 测试基线红线（任何改动后不许下降）

| 项 | 数值（截至 2026-05-17 15:57 复核）|
|---|---|
| Python 单元测试 | 1228 / 1228 通过 |
| Java 单元测试 | 175 收集 / 175 通过（2026-05-18 MarginTrading 加 `historyDerivesDailyNetInflowWhenRepayIsMissing` 覆盖深市无 repay 字段时的余额日差回退）|
| Java 接口存活 | 120+ 个 `@PostMapping` 全部返回 200 + result=0（含 backtest / shadow / sample-progress / data-health 全栈）|

## 2.1 pre-push hook 自动审计（N8）

任何 `git push` 前本地 hook 强制跑 3 项静态 gate(无需 DB):

| Gate | 工具 | 失败原因 |
|---|---|---|
| 1/3 parity | `python/stock-pipeline/tools/check_entity_dataclass_parity.py` | Python dataclass / Java Entity / Flyway 列三层字段漂移 |
| 2/3 null cast | `scripts/audit-mapper-null-cast.ps1` | Java Mapper SQL `#{var} is null` 未 cast 类型 |
| 3/3 sanity test | `pytest tests/test_track_sanity_check.py` | 业务闭环 sanity check 逻辑回归 |

**安装**:`powershell -File tools/dev/install-git-hooks.ps1`(首次 clone 或 hook 源码改动后跑)

**应急 bypass**(限正当理由,如热修需立即上线):`$env:SKIP_PREPUSH_AUDIT=1; git push`

**不在 pre-push**(需 DB 或耗时长,留给 weekly SOP / EOD 跑批):
- `audit_fetcher_date_consistency.py` —— EOD 每周一上午盘前手动跑或入 Task Scheduler
- `track_sanity_check` runtime —— daily_pipeline.py 阶段 11 已集成

## 3. 接到需求时的标准流程

1. 通读 `AGENTS.md` + `docs/architecture/` 下相关文档
2. 用 5 视角评审需求（见 `.codex/rules/roles-5-perspectives.md`）
3. 判断改动落在哪一层：
   - 数据采集 / 算法 → Python
   - 业务接口 / 任务调度 → Java
   - UI / 表单 → 前端
   - 跨层 → 按 **Python → Java → 前端** 顺序推进
4. 设计完成前不动手，先与用户对齐

## 4. 修改前必读清单

任何改动落地前必须确认：

- [ ] 看过 `AGENTS.md` 的角色定位与已完成清单
- [ ] 通读 `docs/architecture/<对应文档>.md`
- [ ] 已用 5 视角评估影响（合规？真实可用？性能？算法？架构？）
- [ ] 确认改动所属轨道（Track 1-5）；Track 4/5 需校验样本门控（50/100/150/200）+ Wilson 区间护栏（见 `.codex/rules/pit-redline-and-tracks.md`）
- [ ] 落点层次明确（Python / Java / 前端 / 跨层）
- [ ] 跨层改动按 **Python → Java → 前端** 顺序推进
- [ ] 与用户对齐方案，不擅自扩大 / 缩小范围

## 5. 快速诊断索引

| 现象 | 排查路径 |
|---|---|
| Java 启动 Flyway 失败 | 看版本号是否乱序，启用 `flyway.out-of-order: true` |
| 接口 500 全报 | 检查 `bash test_endpoints.sh`；常见为 PG NULL 参数缺 cast |
| 前端调用 API 报 type 不存在 | 后端改完 DTO 后没跑 `pnpm run api` |
| 前端枚举不更新 | 没跑 `pnpm run enums` 或没重启 Java |
| Lombok @Data 报 "类 Data 找不到" | IDEA Annotation Processors 未启用，或 pom.xml 缺 lombok 依赖 |
| Python pipeline 0 stocks | 检查 `OPENCLAW_STOCK_ROOT` 是否指向正确的 legacy 工作区 |
| 推荐结果 0 条 | 样例股票全 HIGH 风险被过滤；改 `system_config.portfolio_defaults.max_risk_level=HIGH` |

---

## 已有 assertion 兜底

🟡 部分 assertion 化

测试基线（CI 跑批必跑）：
- Python：`python -m pytest tests/` 1333 用例
- Java：`mvn test` 183 用例
- 任何 PR 测试数下降 → review 红线

环境校验：
- 工具：`python -m stock_pipeline.config.validate`（启动前体检环境变量）
- Flyway 启动校验：自动验证迁移与 schema 一致性

前端生成链路（替代部分 assertion）：
- `pnpm run api` 强制走 Java DTO 真值源
- `pnpm run enums` 强制走 Java 枚举真值源

工具：
- `tools/pre_change_audit.py`（兄弟 C 新加，改动前自动扫规则适用性）

盲区（建议未来补）：
- 缺：CI 钩子在 PR merge 前强制跑 Python + Java + 前端 lint 三件套
- 缺："测试数不下降"的 GitHub Actions 自动检测（diff 测试数 < 0 拒绝 merge）
- 缺：接口存活探测（`test_endpoints.sh` 自动化跑全量 POST + result=0 断言）
- 缺：跨层改动顺序检测（Python → Java → 前端 顺序未走完前 PR 不可合）
