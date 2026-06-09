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

### 🟡 第二梯队:需 WSL daemon(在 WSL 窗口做)

| 任务 | 估时 | 前置 |
|---|---|---|
| Phase 6 接 vector/bm25 lane | 1-2天 | chroma daemon(`serve-mcp start`);recall 再加两 lane,fusion 核心已支持 |
| Phase 6 reranker | 1天 | reranker 模型;压缩候选精排,关闭时降级加权 RRF(plan Gate 已设计) |
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
