# Plan: AI 协作工具栈团队化部署

> 状态:📋 **草案,未启动**
> 创建:2026-05-27
> 关联:`docs/log/2026-05.md`(早上讨论"工具栈 + memory 多人怎么搞")
> 性质:实验性内部 SaaS,**不对外卖**,不开源

---

## 一、定位

把当前 Solo 工具栈(chroma + codegraph + cross-link + 24 rules + 14 memory + BM25 hybrid + post-commit hook + pre-push 6 gates + ai-health)改造成 **5-20 人技术团队可共享的内部服务**。

**不是 SaaS**(无外部客户 / 不卖订阅)、**不是开源**(护城河保留)、**不是产品**(没卖给别人)。**是"团队级 AI 协作纪律基础设施"**,跟自来水管一样,用了就用了。

---

## 二、决策前提

| 前提 | 状态 | 备注 |
|---|---|---|
| 量化主线 CLOSED ≥100 | ❌ 当前 21-28 | 累积期不能动 L2,本 plan 跟 L2 抢精力 |
| 实际有协作者(≥2 人) | ❌ Solo | 没人用 = 做了也没价值 |
| 硬件:服务器 GPU ≥ 16GB | ❌ 当前 RTX 5060 8GB | 8GB 撑不住 5+ 人并发 |
| 用户对接受第三方 MCP 服务的意愿 | ❓ 未验证 | 开发者可能更愿意装本地 |
| Anthropic 是否抢推 Claude Code for Teams | ❓ 监控中 | 2027 大概率 |

**门槛**:这 5 项前提**至少 3 项满足**才启动。否则纸上谈兵。

---

## 三、Phase 划分(7 个,每个独立可中断)

每 Phase 1-2 周(全职估时,副业 ×3)。

### Phase 0:决策 + 前置(1 周)

- 写完整 design doc(本 plan 只是骨架)
- 确认硬件:服务器规格 / GPU 卡 / 内存 / 磁盘
- 决定:开源 / 闭源(再次确认)
- Gate:**用户拍板继续**(否则停在这一步)

### Phase 1:chroma HTTP daemon 改造(2 周)

当前 daemon 已支持 HTTP mode(`PLATFORM_DOCS_DAEMON_MODE=true`,localhost:18083)。

- 暴露到 0.0.0.0(允许远程)
- 加 worker pool(并发 query)
- 加 Bearer token auth
- 加 health endpoint + Prometheus metric
- Gate:**本地 3 客户端连通测试**

### Phase 2:codegraph + cross-link HTTP 化(2 周)

目前每会话 stdio,改 HTTP server。

- 抽出 HTTP API server
- SQLite WAL 模式已就绪(读并发 OK)
- Gate:**5 客户端并发读测试**

### Phase 3:多 workspace 隔离(2 周)

每开发者本地工作树有差异,服务器索引必须正确处理 dirty 状态。

- 服务器索引 = "master + 共享分支"
- 个人 dirty 时降级:MCP 只作参考,关键决策回读本地真实文件
- 加 `git workspace fingerprint` 让 MCP 知道客户端工作树状态
- Gate:**5 人模拟工作树差异 dirty 不踩坑**

### Phase 4:GPU 队列 + 高可用(1-2 周)

GPU 单卡跑 daemon,并发请求队列(防 OOM)。

- queue + rate limit
- 缓存 reranker top-30 短期结果(LRU 5 分钟)
- 失败降级纯向量
- Gate:**压测 20 并发请求 P95 < 5s**

### Phase 5:memory team/personal 分层(1-2 周)

当前 14 条 memory 混了团队约定 + 个人偏好。

- 拆分:**8 条团队约定 → `.claude/rules/`(已在 rules) / 4 条用户偏好 → `personal/<user>/` gitignore / 2 条业务知识 → 移 rules**
- 改 CLAUDE.md autoload 动态识别 user
- Gate:**5 人各有 personal,team 部分共享**

### Phase 6:git webhook + 监控(1-2 周)

- server 监听 GitHub / GitLab webhook
- push 触发增量 reindex
- chroma reindex log 集中查看
- Prometheus + Grafana dashboard
- Gate:**24h 稳定运行,reindex 自动触发率 100%**

### Phase 7:试运行 + 部署 SOP(2 周)

- 内测 5-10 人
- 收集 feedback,迭代 1 轮
- 写部署 SOP(Docker compose / k8s helm)
- Gate:**5 人愿意持续用 ≥ 2 周**

**Total**:**12-14 周**(全职)/ **6-8 个月**(副业 20% 精力)。

---

## 四、风险

| 风险 | 严重度 | 缓解 |
|---|---|---|
| GPU 单卡瓶颈,超 10 人需换卡 | 🔴 高 | Phase 0 决定服务器 GPU 时直接上 16GB+;预算 ¥1.5-3 万 |
| Anthropic 2027 推 Claude Code for Teams | 🔴 高 | 监控官方动态;真出了停 Phase 6/7,本地用够 |
| 开发者抗拒第三方 MCP 服务 | 🟡 中 | 留 Solo 本地模式 fallback(用户可选) |
| 维护成本 7x24 高 | 🟡 中 | Phase 6 自动化监控 + 告警 |
| 跟 L2 量化抢精力 | 🔴 高 | **强制约束**:CLOSED ≥100 前不启 Phase 1+ |
| 试运行 < 3 人或不持续用 | 🔴 高 | Phase 7 内测后**直接停**,不强推 |

---

## 五、替代方案

| 方案 | 投入 | 收益 | 选 |
|---|---|---|---|
| **A** 不做,保持 Solo + AI(L3-A 内部用) | 0 | 0 | 默认 |
| **B** 写博客分享方法论,不做产品(L3-B) | 15% 副业 | 个人品牌 / 咨询线索 | 5-31 后可启动 |
| **C** 本 plan(L3-C 团队部署) | 12-14 周 / 80% 精力 | 真有协作者时变现 / 自用 | ❓ 等前提满足 |
| **D** 开源核心 + 闭源企业版 | 类似 C + 社区运营 | 影响力 | ❌ 你已否决开源 |

---

## 六、启动条件(满足前不进 Phase 1)

**必须全部满足**:

1. ✅ 量化主线 **CLOSED ≥100**(预计 2026-07 初)→ Track 4/5 解锁后业务节奏稳
2. ✅ 至少有 **1 个真实潜在协作者**(同事 / 朋友 / 早期内测者)表达兴趣
3. ✅ 服务器硬件就绪(GPU 16GB+ / 内存 32GB+)
4. ✅ 用户再次拍板"开干"

**任一缺失 → 本 plan 保持草案状态,不进 Phase 0**。

---

## 七、状态追踪

| 日期 | 状态变化 | 备注 |
|---|---|---|
| 2026-05-27 | 📋 草案创建 | 早上讨论"团队化 + memory 多人" 的产物 |
| — | — | 等启动条件满足 |

**未来更新本表,不删历史行**。

---

## 八、关联

- 上午讨论上下文:`../log/2026-05.md`(具体哪条决策提到的)
- 工具栈现状:`../../ai-dev-architecture-map.html`
- 当前 memory:`../../memory/MEMORY.md`
- 累积期纪律:`../../../.claude/rules/pit-redline-and-tracks.md`
