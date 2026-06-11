# Next Steps — 2026-06-09(roadmap-2026-06-07 续)

> 本文件是 `daily-summary-2026-06-09.md` 的"接下来做什么"配套,供新窗口直接 Read 开局,不用粘贴。
> 仍属 roadmap-2026-06-07 迭代(代码智能平台);未另起 06-09 目录(同一轮迭代,06-09 daily-summary 已在本目录)。

## 0. 新窗口开局动作(必做)

```bash
# 1) 让上会话 #1/#2 安全修复 + code_recall agent 工具 + planner 主检索 live
sudo systemctl restart codev-agent codev-web   # WSL, 密码 123456
# 2) 验栈
ai-health --all
```

> 上会话改动在 agent/web 代码,服务未重启则不生效。codev-mcp-graph 上会话已重启,可不动(重启后 /mcp 会 churn 重连)。

---

## 一、审计剩余 2 项(已处理 10/11,剩这 2)

### ✅✅ #10 只读 audit —— 已完成(`4cdf204`,审计 → 10/11)

`audit_all_stores` 改 read-only 连接(`file:?mode=ro`),不再 mkdir/WAL/迁移/DDL;旧 schema 读不动记 `audit_error`(计 1 error 不崩门禁);`render_markdown` 渲染原因;删 `open_store` 导入。测:`test_graph_audit` 11/11(只读不改字节 + 旧 schema 优雅记错 + 空目录不建录);真机 `graph audit --all` 跑通 codev-platform 457 节点/607 边 read-only exit 0。

**测**:`tests/test_graph_audit.py` 加"只读连接审计不产 WAL / 不改文件";现有 audit 测试回归。

### ⏸️ #7 config DI(改动大、价值低,真要彻底才做,~半天)

**问题**:`web/routes/jobs.py:28`、`agent.py:27`、`web/security/sessions.py:222`、`session_authenticator.py:41` 在 import 时 `load_config()` 绑单例;`create_app(cfg)` 的显式 cfg 只作用 app factory + authenticator → 测试注入 cfg / 多实例时可能 authenticator 用 cfg A、session/job 用 cfg B。

**轻量做法**:加 `rebind_web_services(cfg)`,把那几个单例绑定收进一个函数,`create_app(cfg)` 启动时统一重绑。

**注意**:单实例生产 config 一致(同一 `load_config`),影响主要在测试/多实例 → 价值低。改时盯 import 顺序 + 现有绑定流程别破。

### ⏸️ #8 残留:set_roles 多 org 角色管理(安全敏感,要时再做)

**问题**:`web/services/user_service.py:134` `if org_id != user.org_id: raise "org_id 与用户归属组织不一致"` —— `user.org_id` 是登录默认首 org → admin 无法给用户在第 2 个 org 设角色(与多 org 模型冲突)。

**正确做法(需谨慎)**:把 `org_id == user.org_id`(单一归属)改成校验目标用户是 `org_id` 的成员(或 caller 是 `org_id` 的 admin + 正在加入);`_guard_same_org` 也从"首 org"改成"目标 org"判定。

**注意**:RBAC 安全代码,改错会造跨 org 越权。要动:先补多 org 越权测试(外组 admin 不能写、目标用户非成员处理),WSL 跑 `test_session_project_access` + RBAC 测试。不在常规流里顺手改。

---

## 二、整体剩余计划(按"新窗口可纯后端验证 + ROI"排序)

### 🟢 第一梯队:纯后端、新窗口可完整验证(推荐先做)

| # | 任务 | 估时 | 价值 | 起点 |
|---|---|---|---|---|
| 1 | ✅ 精准化 recall eval golden(`4cdf204`)| 0.5天 | 中 | 已做代码侧:`_recall_per_query` 排除测试 ref(复用 `_is_test_hit`)。**剩**:真实 MRR/nDCG 复跑需 push + WSL pull 后 `--suite recall`(Windows skip, 需 codegraph.db) |
| 2 | ✅ #10 只读 audit(`4cdf204`)| 0.3天 | 低(清爽收尾) | 已完成, 见 §一 #10 |
| 3 | Phase 7 完整版 | 2-3天 | 中-高 | LLM planner(可选增强,现为关键词)+ agent 端到端 eval(测完整回答质量,非只分类准确率)。复用 `agent/planner.py` + eval planner suite |

### 🟢 Panel 评审后执行(2026-06-11,eval 驱动)

- **✅ recall 金标扩 24→35**(+11 general 语义类,`d27371d`):旧集全 symbol/impact 遮蔽天花板(panel #1 共识:瓶颈是缺 eval 不是缺 lane)。query_type 经 classify_query 确认、expect 经 token_match 验证。
- **✅ vector 嵌入加源码片段**(`fb12f99`):扩集暴露 vector 在 general/中文行为查询上很弱(找到 3/11)→ 诊断真因 codegraph docstring 覆盖仅 7-11%、嵌入文本无语义(非 query-prompt,实测 query-prompt 只 +1/11)→ `_embed_text` 读 start_line..end_line 源码片段补 docstring。两项目重建后 general 找到 **3→7/11**、MRR 0.195→0.422、**vector delta +0.331 CI[0.101,0.594] 转统计显著**。剩 4 长尾=0.6B 模型极限(换大模型 trigger-gated)。
- **panel 其余裁决**:bm25 lane=**drop**(被 codegraph-FTS+vector 夹的冗余);reranker=**保持默认关**(扩集上 paired CI 下界>0 才开,代码已就绪);memory lane=**绝不进 recall_code 核心**(无身份入口塌缩 default/local 跨 org 红线,要做只在 web 身份齐入口分区编排);前端=清 services.ts + soft-quality 卡片 do、3D 可视化 defer;重型 Phase 2/4/8=trigger-gated,Phase 3 残留顺带补。

### 🟡 第二梯队:需 WSL daemon(在 WSL 窗口做)

| 任务 | 估时 | 前置 |
|---|---|---|
| ✅ Phase 6 vector lane(已落+已验证+已上线+已打磨,06-11)| — | **代码**:`recall/code_vector_store.py`(对 codegraph 节点嵌入,ref 复用 node id→与 codegraph lane 同空间叠分,每项目独立 persist 目录)+ `service._vector_lane`(fail-soft)+ `iter_nodes`。**建库**:两项目(kind 过滤后 codev 4936 / openclaw 10053 节点,排除 import/file/variable 噪声)。**eval 实证(大胜)**:等权 3-lane vs 2-lane,codev MRR 0.516→0.944(+0.43)/openclaw 0.347→0.917(+0.57),四 CI 全正。**两个 bug 修复**:① "偏好 vector 2x"显著更差→回均衡(`537046c`);② kind 噪声(query 召回全 import 节点)→ blocklist 排除(`f80912e`);③ 多批 upsert compaction 522→全量重建 rmtree 整目录(`13d6a63`)。**上线**:codev-mcp-graph/agent/web 已重启,`recall_code` 工具即用 vector lane(rebuild 为 build 侧改动,服务直读 chroma 无需再重启)。**LLM e2e 验证**(hard 集 codev 20 例真 deepseek):grounding ON 0.95/OFF 0.85,delta +0.10 CI 跨 0 不显著(天花板:16/20 两边满分),但拆 case 级 2-lane 全挂的 3 例 vector 全救回(代价 1 例良性回归,零幻觉),效率零回归 → 检索层确凿+e2e 正向无害,收口。**✅ reindex worker 自动刷新已接**(`ebb517c`):code_vec 加增量重建(manifest id→text sha1 diff,只重嵌变更节点;无 manifest 退全量 rmtree)+ `reindex --code-vec` stage(4/4,codegraph 后,失败隔离,--force=全量否则增量)+ runner 注册 + webhook/dispatch 在 codegraph scope 命中时 append code_vec(排 codegraph 后保新鲜)。两项目 manifest 已建(codev 4948/openclaw 10053);实测增量重跑「变更 0」0.9s(vs 全量 5min);worker 端到端验通过(enqueue code_vec→worker 跑 step4/4 rc=0);codev-reindex/webhook 已重启加载。**vector lane 全功能交付完毕。** |
| Phase 6 bm25 lane | 0.5天 | recall 再加一 lane;同 fusion 接线模式 |
| ✅🟡 Phase 6 reranker(已建+config 门控,06-11)| — | `recall/rerank.py`:融合后 top 候选用 Qwen3-Reranker(复用 chroma daemon /rerank)精排;精排文本走 codegraph node() name+sig+docstring(与 vector 同源)。**config `recall.rerank.enabled` 默认关 → 关/不可用/异常退加权 RRF**(plan Gate「reranker 关仍稳定」),fail-soft 绝不丢结果。测 `test_recall_rerank.py` 全绿。**A/B(同候选集 on vs off)**:两项目 nDCG 稳定 +0.078/+0.075、零回归,但 CI 触/跨 0 不显著(小样本)+ 加 ~100-200ms 延迟。**默认关**(modest 收益 + 延迟成本 + 用户 config 主权);要开:WSL config 设 `recall.rerank.enabled=true` + 重启 codev-mcp-graph/agent/web |
| Phase 6 memory lane | 0.5天 | PG(agent memory);把 agent/recall 接进 fusion |

### 🔵 第三梯队:前端(需 pnpm/tsc,本机 3D 内存风险)

| 任务 | 价值 | 备注 |
|---|---|---|
| soft-quality dashboard 卡片 | 中 | 端点 `/api/v1/graph/soft-quality` 已就绪,补前端卡片 |
| find_impact_paths 路径可视化 | 中 | Phase 5 已出数据,前端画依赖路径 |
| 清理 `codegraph/common/services.ts` 旧封装 | 低 | 见记忆 frontend-no-services-wrapper,组件直调生成接口 |

### ⚫ 重型地基(真实需求触发再做,plan §六纪律)

- **Phase 2 统一 IR 解析引擎**(3-5天+):纯未启动,最重。
- **Phase 8 查询响应性能**(2-4天):纯未启动。
- **Phase 4 社区检测**(Louvain/Leiden)→ 解锁 Phase 5 社区/新鲜度加成(现 path scoring 缺这两项)。
- **Phase 3 残留**:`source_line`/`index_manifest_id` 落每边。

---

## 三、推荐顺序

~~先重启上线 → #1(eval golden)→ #2(#10 收尾审计)~~ **已完成(`4cdf204` + 上线验证)**。
下一步:**Phase 7 完整版**(第一梯队最后大项)或 **push + WSL 复跑 #1 拿真实 MRR/nDCG**,或第二梯队 WSL 任务。
