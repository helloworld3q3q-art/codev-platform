# Plan — 按客户分档的业务域标注(规则/hybrid/LLM)(2026-06-11)

> **🧊 状态:搁置待做(DEFERRED)。** 8 视角面板 + 真图谱实测后裁决:**现在不做**,留到触发条件成立。
> 本文件沉淀全部分析/设计/实测,届时直接落地,不用重推。
>
> 承 2026-06-11 会话(daily-summary-2026-06-11)关于 A1/A2 域标注省钱/提效/按客户可选模型的讨论。

---

## 一、为什么搁置(裁决依据)

用户诉求:标注"省钱 + 提效 + 按客户可选 deepseek/其他/不用",平台云托管多租户。8 视角面板 + 2 个真图谱原型后结论:

1. **标注是成本小头**。云账单大头是 **agent 对话 loop**(每用户每提问多步循环,每租户每天数十万~数百万 token),标注是 O(域数) + fingerprint 缓存(增量重建大半命中),**小 2-3 个数量级**。优化标注省的钱 ≈ 总盘噪声。
2. **正确率不涨**。最好持平;纯规则**不设置信门实测 27% 静默错**(见 §六)。要"不变差"就得搭门 + 好词典 + golden 验证。
3. **YAGNI**。架构已支持(`DomainLabeler` 协议 + `analyzers.<name>.provider` 配),真有需求时 1-2 天可加。现在建 = 为不存在的需求囤代码。

**hybrid"省钱档"明确不做**(省的是芝麻 + 加噪音风险)。唯一有产品意义的是**纯规则免费/离线档**(见 §二)。

## 二、触发条件(满足任一才启动)

- 出现**具体客户**:选"免费/离线档、不用任何付费 LLM",但仍要业务域名(接受部分覆盖)。
- 或:数据合规要求标注**不出网**且**不接受自托管模型**(否则自托管模型档一行 config 即可,无需规则器,见 §八)。

不满足 → 保持搁置。

## 三、范围

**做(仅触发后)**:
- 纯规则标注档(零 LLM,免费/离线档功能可行性)。
- 分档骨架(rule | hybrid | llm 可配),让 deepseek/本地模型/规则按客户选。

**不做**:
- hybrid 作为"省钱"手段(小头,被 loop 淹没)。hybrid 档可作为骨架一员存在,但**不以省 token 立项**。

## 四、设计(架构视角,复用现有协议,核心零 if-else)

仿 provider registry 做 **labeler registry**,三档全部实现同一 `DomainLabeler` 协议(`label(batch)->list[ClusterLabel]`):

| 档 | 实现 | 正确率(预期) | LLM 调用 |
|---|---|---|---|
| `llm`(默认,现行为) | 现 `BrainDomainLabeler` | ~95% | 100% |
| `hybrid` | 规则+词典直出高置信,LLM 兜底低置信 | ~95%(持平,**非升**) | 实测 ~↓46%(非 65-70%) |
| `rule` | 纯规则,零 LLM | ~部分覆盖(实测 46% 出名/54% 未分类) | 0 |

- `analyzers.business_domain.labeler` 选档,默认 `llm`(不破存量)。
- **缓存键按档加前缀**(`rule:` / `hybrid:{dict_ver}+{model}` / `llm:{model}`)防档间互相污染;同租户换档=切到另一组缓存条目而非清空,换回命中旧缓存不重标。
- **per-租户配置**入 `platform_meta/projects/<pid>/meta.json` 的 `analyzers.business_domain` 段,与现有 `provider` 覆盖同源,云托管按 project_id 隔离。

## 五、核心机制:置信门(防静默错,不是可选项)

实测证明"取最高频词根"会 27% 静默错(混合簇主导词根猜错)。所以:

- **置信公式**:`conf = 0.6·词根纯度 + 0.3·词典覆盖 + 0.1·成员共识`(纯度=top 词根占比,共识=成员投同一域比例)。
- **阈值**:`conf≥0.80` → 词典直出;`0.55–0.80` → 出名标"待核";`<0.55`/多义/未命中 → **弃权转 LLM**(rule 档则标"未分类")。
- **铁律**:低置信**必须弃权,禁止凑名**(静默错比 LLM 弃权更毒)。
- **防混合簇**:成员共识低(如 user 表跨多域)→ 判多义转 LLM,禁单表定域。

## 六、实测数据(原型,openclaw 26 真实域,确定性零 LLM)

粗种子词典 + 取最高频词根(**未设置信门**):
- AGREE(规则==LLM)12(46%)/ CLOSE 2 / **DIVER(静默错)7(27%)** / MISS(未分类)5(19%)。
- DIVER 实例:选股推荐→错"交易计划"(plan 票压 recommend)、用户鉴权→错"角色管理"(role 票压 auth)。
- **结论**:无门 = 27% 噪音;DIVER 全是低纯度混合簇 → 置信门(§五)正好拦下送 LLM。
- **现实省幅**:规则干净直出 ~46%(非乐观估的 65-70%);免费档纯规则 ≈ 46% 出名 + 54% 未分类(词典做厚可提,但每提一点换噪音风险)。

## 七、上线门(做任何档前的铁律,不可省)

1. **独立人工 golden**:N≥300-500 分层(简单/难/边界),**禁拿 deepseek 输出当真值**(现 `code_intelligence` golden 若如此即循环作废,先证尺子)。
2. **Wilson 95% CI 非劣**:候选 CI 下界 ≥ baseline−δ(δ 如 2 点);声称"提准"需下界 > baseline(大概率证不出)。
3. **静默错率硬门禁**:专测规则"命中但错"率 vs 弃权率 vs 兜底触发率;静默错率不达标**直接否**。

守 [[recall-weight-ab-finding]] / [[anti-false-premise-phaseA-falsified]] 纪律:无 golden 不许声称提准。

## 八、不在本 plan / 更大杠杆(真"省钱"在这)

- **自托管模型档零代码**:`analyzers.business_domain.provider` 指向本地 OpenAI 兼容端点(Ollama/vLLM)即满足"不出网/不用 deepseek",质量仍 LLM 级。3 个坑:key 必填非空占位、端点须 /v1 风格、顶层 `agent.model` 不能残留(全局覆盖会串台,见 [[agent-model-config-footgun]])。**这条不需要本 plan,先于规则器。**
- **真省钱 = agent loop**(大头):prompt-cache 命中(deepseek cache-hit $0.0028 vs miss $0.14,省 ~90% 重复 input)、recall top-N/context 收紧、per-档 max_steps 限、per-租户用量计量+配额。**另立 plan / 优先于本标注 plan。**

## 九、最小改动清单(触发后)

- 新 `codev_platform/graph/analyzers/labeler_registry.py`(register/get,按 config 选档,默认 llm)
- 新 `codev_platform/graph/analyzers/rule_labeler.py`(`RuleDomainLabeler` + 置信门 + 词根抽取)
- 新 `codev_platform/graph/analyzers/hybrid_labeler.py`(rule + 注入 llm fallback,低置信批量兜底)
- 新 `codev_platform/resources/domain_lexicon.zh.yaml`(词根→中文域名,平台级共享资产 + pyproject package-data;客户可 `lexicon_path` 覆盖)
- 改 `BusinessDomainAnalyzer` 构造一行:`BrainDomainLabeler(cfg)` → `get_labeler(cfg)`(落地前核对该处真实写法)
- `meta.json` 加 `analyzers.business_domain.{labeler,hybrid_threshold,lexicon_path}` 三键(纯 config)
- 测试:rule/hybrid 纯函数单测 + §七 golden 非劣 + 静默错率门
- 上线:reindex worker 重建产出(labeler 缓存命中不重跑),WSL 见 [[llm-analyzer-deploy-three-steps]]

## 关联

- 分析来源:2026-06-11 会话 8 视角面板(IR/产品/架构/测量 ×2 轮)+ 2 真图谱原型(`verify_phase4.py` / `rule_namer_proto.py`)。
- 记忆:[[a1-business-domain-analyzer]](聚类 file 主信号 95%)、[[code-quality-principles]](registry/协议扩展)、[[agent-design-multi-model-first]](分档不硬编单模型)。
