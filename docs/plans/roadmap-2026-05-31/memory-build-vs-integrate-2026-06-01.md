# 记忆层战略决策:自建 M5 vs 接入 supermemory / mem0 (2026-06-01)

> 决策性质:战略取舍,非代码红线。决定记忆引擎(M1-M6)后续是否继续大投入,还是接外部产品把精力收回代码智能差异化。
> 关联:`../roadmap-2026-05-29/memory-permission-model-2026-05-29.md`(M1-M6 设计 + RecallService 接缝)。

---

## 一、背景:2026-06-01 的新发现

调研 [supermemory](https://example.invalid/reference) —— 一个**通用记忆 API 产品**(SaaS + 可自托管),与本平台 M1-M5 记忆引擎设计**高度重叠**:scope 作用域、supersede(替代)、forget(遗忘)、TTL、冲突策略、混合召回(向量 + 关键词)。`mem0`(github.com/mem0ai/mem0)同类。

关键差距:它们**远更成熟** —— 公开 benchmark 领先、连接器(Notion / Slack / Google Drive 等)、多模态、SDK 生态(JS / Python / REST)、托管运维。本平台 M1-M5 是自建,只覆盖核心闭环,无生态。

**对标思路**:supermemory 用 LongMemEval 等公开 benchmark 证明其记忆召回价值;本平台的差异化**不在记忆本身**,而在 codegraph + cross-link 的代码智能(eval harness 本批正是为量化这点而建)。

---

## 二、本平台现状

- M1-M4 自建已落地 + 真 PG 验证(2026-05-29,`verify_memory_pg.py` 全绿):身份 / 4 层 scope 存储 / 跨作用域召回 + 冲突 / TTL + supersede + 压缩。
- M5(ACL 可见性)接缝已留(`visible_scopes` / `resolve_conflicts(policy)` 参数化),待第二人验证;M6(RBAC + 审计 + secrets)等真多团队。
- **记忆只是平台的一个平面**。真正的差异化是 codegraph(符号图谱)+ cross-link(跨层业务链路)—— 这两块市面记忆产品**不做**,是本平台对 AI 协作的独特增益。

---

## 三、决策矩阵:自建 vs 接入

| 维度 | 自建 M5/M6 | 接入 supermemory / mem0 | 倾向 |
|---|---|---|---|
| 数据不出内网 / 合规 | ✅ 全程本地 PG,数据主权在手 | 🟡 SaaS 出网;自托管可缓解但运维转嫁 | 自建(若硬需求) |
| 成本 | 🟡 已沉没(M1-M4 已写);后续维护人力 | 🟡 SaaS 按量计费 / 自托管省钱但要运维 | 平 |
| 成熟度 / benchmark | ❌ 无公开 benchmark,召回质量未量化 | ✅ 公开 benchmark 领先,生态成熟 | 接入 |
| 锁定风险 | ✅ 零外部依赖 | ❌ API / 数据格式锁定,迁移成本 | 自建 |
| 与 RBAC / 多租户耦合 | ✅ 已与项目 ACL(模型 C)同源对齐 | 🟡 需把外部记忆映射进本平台 namespace/ACL | 自建 |
| 维护负担 | ❌ 自己扛 bug / 升级 / 索引 | ✅ 厂商扛 | 接入 |
| 离线 / 私有化部署 | ✅ 原生支持 | 🟡 仅自托管版,且非核心场景 | 自建 |

**净结论**:记忆能力本身正在**半商品化**(supermemory/mem0 把它做成 API);自建的唯一不可替代价值是**数据主权 + 私有化 + 与本平台 RBAC/多租户深度耦合**。若这些不是硬需求,继续投入自建记忆边际收益低。

---

## 四、关键权衡(一句话)

- 若 **"数据不出内网 + 自托管私有化"是硬需求** → 自建合理(已沉没成本 + 接缝已留,M5 只差 ACL 读取)。
- 若不是 → 记忆半商品化,接 supermemory/mem0、把精力收回 codegraph/cross-link 更划算。

---

## 五、推荐(有倾向,留口)

**默认:保留自建 M5,但战略重心明确压向代码智能差异化。**

1. **保留自建 M5**:私有化 / 合规驱动 + M1-M4 已落地,M5 仅差"ACL 可见性计算 + orgs.conflict_policy 从 PG 读",接缝已留,完成成本低。
2. **记忆不再投大改**:M6(RBAC / 多模态 / 连接器)**不主动建**,等真多团队需求 + 压测证据;不追 supermemory 的生态广度。
3. **战略重心 = codegraph + cross-link**:这是市面记忆产品不做的差异化,本批 eval harness 量化它,后续迭代主要投这里。
4. **留接缝不变**:`RecallService` 抽象(plan §3.9)已能换实现 —— 未来若切外部记忆,加一个 `HttpRecallService` / `SupermemoryRecallService` 实现,上层 agent 零改。

---

## 六、触发"切外部记忆"的条件清单(届时照做)

满足以下**任一**即重新评估接入 supermemory/mem0:

- [ ] 数据出网 / SaaS 合规上获批(私有化不再是硬约束)。
- [ ] eval 显示自建记忆召回质量显著落后公开 benchmark,且自行优化 ROI 低。
- [ ] 需求扩到多模态记忆 / 第三方连接器(Notion/Slack 等)—— 自建生态成本过高。
- [ ] 记忆维护占用挤压 codegraph/cross-link 主线投入。
- [ ] 记忆量级增长到自建向量后端 + 运维不划算(supersede/compress 扛不住)。

切换动作:实现 `RecallService` 的外部适配器(走 `config.memory.recall_backend`),迁移现有 PG 条目,agent 上层不动。

---

## 七、行动项

- [ ] 配合本批 eval harness,**给记忆也建评测**:对标 LongMemEval 思路,造跨会话偏好 / 事实召回用例,量化自建 M1-M4 的召回质量(命中率 / 冲突解析正确性)。有了 baseline 才能在"触发条件"里客观判断是否落后外部产品。
- [ ] M5 仅补"ACL 可见性 + conflict_policy 从 PG 读"闭环(接缝已留),不扩 M6。
- [ ] 本文档结论同步 `docs/log/2026-06.md` 月决策聚合。
