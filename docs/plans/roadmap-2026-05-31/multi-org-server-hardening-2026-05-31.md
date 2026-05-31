# 多组织服务器加固 Plan(2026-05-31)

> **定位**:把 codev-platform 从"单人开发机能跑"推向"多组织(多租户)/ 多人 / 多项目可安全共享的服务器"。
>
> **前提(用户拍板)**:部署形态是「多团队(多组织)多人多项目」,「一上来就要细到人/项目」。开发环境求全、部署后再按生产优化;开发与生产是两套规则。
>
> **关联**:`../roadmap-2026-05-29/memory-permission-model-2026-05-29.md`(memory 权限 M1-M4 已落地,M5 ACL 接缝已留)。本轮**项目 ACL** 与其 §3.4 授权模型**同源**:org 是租户根,project 挂 org,身份带 project 白名单。

---

## 一、现状盘点(还没做的开发,按域分)

### A. 安全 / 多租户隔离(上线多组织前必须)
| # | 项 | 现状 | 风险 |
|---|---|---|---|
| **A1** | **项目 ACL 隔离(chroma/codegraph/cross-link/agent/memory)** | ❌ | passthrough 下任何能连端口者可查**任意项目**全部数据。多组织最大洞 |
| A2 | token 模式实际启用 | 🟡 CLI 已建,默认 passthrough | 没强制 = ACL 形同虚设 |
| A3 | 传输层 TLS / 反代 | ❌ 裸 HTTP 19083/19086/19091/19099 | 跨机 token 明文裸奔 |
| A4 | 速率限制 / 滥用防护 | ❌ | — |
| A5 | 按身份审计日志(谁查了哪个项目) | 🟡 仅 usage jsonl | 无 per-identity 追溯 |

### B. 持久化 / 可靠性
| # | 项 | 现状 |
|---|---|---|
| B6 | Memory/session PG 持久化(`psycopg_pool` + DSN) | 🟡 代码就绪(M2),库待建 / 包待装 |
| B7 | 数据备份(chroma data / cross_layer.sqlite / codegraph_ext / gitea) | ❌ 全无 |
| B8 | WSL 时钟漂移根治(定时重同步) | ❌ |
| B9 | 服务崩溃自愈(`Restart=always` 全覆盖核验) | ❓ 待核验 |
| B10 | cross-link 重建脚本 | ❌ 规则标"待补" |

### C. 运维 / 可观测
| # | 项 | 现状 |
|---|---|---|
| C11 | 指标采集 + 告警 | ❌ 仅 health 端点 |
| C12 | 集中日志 | ❌ 各服务各写文件 |

### D. 多用户接入体验
| # | 项 | 现状 |
|---|---|---|
| D13 | 组织/用户/项目管理面(建 org / 建 user / 项目归属) | 🟡 仅 token CLI |
| D14 | 业务仓 `.mcp.json` 带 token 模板生成 | 🟡 init 生成基础版,token 未自动注入 |
| D15 | 新组织/新人 onboarding 部署文档 | ❌ |

### E. Agent(LLM)侧(若启用)
| # | 项 | 现状 |
|---|---|---|
| E16 | Agent HTTP 服务常驻 systemd | ❌ 非常驻 |
| E17 | Agent provider key 按组织隔离 | ❌ 未设计 |

### F. 索引数据面
| # | 项 | 现状 |
|---|---|---|
| F18 | 新项目自动 link codegraph | 🟡 手动 `link --all` |

---

## 二、优先级路线

```
真正卡多组织上线 = A 组(尤其 A1 + A3)。B/C = 多人用起来后会被坑的健壮性。D/E/F = 体验/扩展。
```

| 序 | 阶段 | 内容 | 状态 |
|---|---|---|---|
| 1 | **P1** | **A1 项目 ACL**(本文档 §三详细设计) | ✅ 已落地+推送(`25101a9`) |
| 2 | P2 | A2 token 模式启用(`token-add --projects` + runbook)+ A5 越权审计日志 | ✅ 已落地+推送(`dcf16a1`/`59edc48`) |
| — | 审计修复 | 7 项审计(token 强制项目上下文 / 入口集成测试 / prod 日志 / 白名单校验 / webhook 限流 / memory 作用域闸)+ SSE 回退顺序 blocker | ✅ 已落地+推送(`5ee329b`) |
| 3 | P3 | A3 TLS 反代(caddy runbook + `client-url` 远程地址重写)+ A4 health 拆分(`/healthz` public / `/platform/status` 鉴权) | ✅ 已落地+推送(`66c1c15`) |
| 4 | P4 | B6 PG 持久化收尾 + B7 备份脚本 | ⏳ 后续 |
| 5 | P5 | B8/B9/B10 可靠性 + D13/D14/D15 体验 + 余项 | ⏳ backlog |

> **A 组(多组织上线必须)基本闭环**:A1 ACL ✅ / A2 token+审计 ✅ / A3 TLS 反代 ✅ / A4 health ✅ / A5 审计日志 ✅。剩 A4 的 `/platform/status` 鉴权已随 P3 落地。测试基线随各轮升至 **218 passed / 6 skipped**。
>
> **明确留后**:#2 memory **org/team 完整 RBAC** = M5(需 PG `org_members`/`team_members`,见 `../roadmap-2026-05-29/memory-permission-model-2026-05-29.md`);本轮仅 interim(token 模式 org/team 默认拒)。

---

## 三、P1 详细设计 —— 项目 ACL 隔离(模型 C)

### 3.1 现状洞

3 个 MCP SSE 服务(chroma `handle_sse` / cross_link `handle_sse` / codegraph `handle_sse`)+ agent `/chat` + memory recall:
- 经 `?project_id=X` 路由到对应租户数据;
- `AuthMiddleware` 已能解析 `Identity{user_id, org_id, via}`(passthrough 头 / token Bearer);
- **但拿到 project_id 后,无任何"此身份能否访问此 project"的校验** —— 给哪个 project_id 就查哪个。

### 3.2 模型 C(用户已定):org 归属 + per-token 项目白名单

两道闸,与 memory §3.4 同源:

```
闸 1(org 隔离):project 属于某 org(config.projects.<pid>.org_id)。
               身份的 org_id ≠ project 的 org_id → 拒。
闸 2(项目白名单):token 携带可访问项目集 projects=[...] 或 "*"(全部)。
               project_id ∉ 白名单 → 拒。
```

**dev / prod 双模式**(用户:开发求全,生产两套规则):
| 模式 | 触发 | ACL 行为 |
|---|---|---|
| **passthrough(dev 默认)** | `gateway.mode != token` | `can_access` 恒放行(明文头不可信,强制无意义);仅记一条 advisory 日志 |
| **token(prod)** | `gateway.mode == token` | 硬校验两道闸,失败 **403** |

→ 开发机零摩擦;部署切 token 模式即全栈强隔离,**代码一处 `can_access`,行为按 mode 分**。

### 3.3 `core/acl.py`(新,纯函数,单一真值源)

```python
def can_access(cfg: dict, identity, project_id: str) -> AccessDecision:
    """身份能否访问 project_id。纯函数,无 IO,3 个 MCP + agent + memory 共用。

    - passthrough 身份(identity.via == 'passthrough')→ allow(advisory)
    - token 身份:
        闸1 org:project 的 org_id 缺省(未登记)= 公开;否则须 == identity.org_id
        闸2 白名单:identity.projects == '*'(ALL)或 project_id ∈ identity.projects
      两闸全过 → allow;否则 deny(reason)
    """
```

返回 `AccessDecision{allowed: bool, reason: str, advisory: bool}`(advisory=passthrough 放行,便于审计区分"真授权"vs"dev 放行")。**零 if-else 分支堆叠**:mode 判定收敛在一处,闸逻辑线性。

### 3.4 `Identity` 扩展(`gateway/auth.py`)

```
Identity 增 projects 字段:
  - PassthroughAuthenticator → projects = ALL(dev 信任)
  - TokenAuthenticator       → 从 token 元数据读 projects(默认 [],显式 "*" = ALL)
                               org_id 同样从 token 元数据读(已有 org_id 字段,补 projects)
```

token 元数据来源:`config.gateway.tokens.<sha256>.{org_id, projects}`(token CLI §3.7 扩充)。

### 3.5 五个执行点(全部调同一 `can_access`)

| 执行点 | 文件 | 插入位置 |
|---|---|---|
| chroma SSE | `chroma/server.py` `handle_sse` | pid 解析后、绑定 contextvar 前 → deny 即 403 |
| cross-link SSE | `cross_link/server.py` `handle_sse` | 同上 |
| codegraph SSE | `codegraph/server.py` `handle_sse` | 同上 |
| agent /chat | `agent/routes/chat.py` | `_resolve_project_id` 后校验,deny → 403 |
| memory recall | `agent/recall_service.py` | recall 入口先 `can_access`(project 闸),再算 `visible_scopes`(memory 作用域闸)|

> memory 侧 **两层**:先项目级 `can_access`(本轮),再 memory 作用域 `visible_scopes`(M5 接缝)。两者正交不冲突。

### 3.6 config schema(`config.example.json` + 文档)

```jsonc
{
  "projects": {
    "openclaw-stock": { "repo_path": "...", "org_id": "default" }  // 新增 org_id
  },
  "gateway": {
    "mode": "passthrough",            // passthrough(dev) | token(prod)
    "tokens": {
      "<sha256-of-token>": {
        "user_id": "alice",
        "org_id": "acme",
        "projects": ["openclaw-stock"] // 或 "*" 全部
      }
    }
  }
}
```

向后兼容:`org_id` 缺省 = 项目公开(单 org 期不破现状);`projects` 缺省 = 空(token 模式下 = 无权,须显式授权 —— 安全默认)。

### 3.7 token CLI 扩充(`ops/gateway.py`)

`gateway token-add` 增 `--org` / `--projects pid1,pid2|*` 选项,写进 `tokens.<hash>.{org_id, projects}`;`token-list` 展示归属 org + 项目白名单。

### 3.8 测试(`tests/test_acl.py` + 各执行点)

- `can_access` 纯函数:passthrough 放行 / token 闸1 org 不符拒 / 闸2 白名单不含拒 / "*" 放行 / org_id 缺省公开放行 / 两闸全过放行。
- 各执行点:token 模式下越权 project_id → 403;passthrough → 放行 + advisory 日志。
- 回归:165 现有测试不降。

---

## 四、后续阶段 backlog(本轮不做,排期可继承)

- **P2 A2+A5**:`gateway mode token` 启用流程文档 + 越权访问写审计日志(per-identity,谁/何时/查哪个 project/allow|deny)。
- **P3 A3**:caddy/nginx 反代 + TLS 终止配置样例 + 业务仓 `.mcp.json` 切 `https://`;服务仅监听 127.0.0.1,反代对外。
- **P4 B6+B7**:`psycopg_pool` 装包 + 建库脚本固化 + 定时 `pg_dump` / chroma data / sqlite / gitea 备份脚本 + 保留策略。
- **P5**:B8 时钟重同步 systemd timer;B9 核验 6 unit `Restart=always`;B10 cross-link 重建脚本;D13 org/user 管理 CLI(对接 memory PG 表);D14 token 注入 `.mcp.json`;D15 onboarding 文档;E16 agent 常驻;F18 webhook 触发自动 link。

---

## 五、验收(P1)

- [x] `core/acl.py:can_access` 落地 + 纯函数单测全绿
- [x] `Identity.projects` + TokenAuthenticator 读 token 元数据
- [x] 3 MCP + agent + memory 五执行点接入,token 模式越权 403 / passthrough 放行
- [x] config.example + token CLI 文档同步
- [x] 165 现有测试不降 + 新增 ACL 测试通过(当前 218 passed / 6 skipped)
- [x] 审计兄弟核查:零 if-else 堆叠 / 与 memory §3.4 同源 / dev-prod 双模式正确

---

## 六、进度记录(2026-06-01)

按 P1→P2→审计修复→P3 推进,全部提交并推送 Gitea(`dev`),测试 165→218 passed。

| 轮次 | commit | 关键交付 | 备注 |
|---|---|---|---|
| P1 ACL | `25101a9`(+`61f1f45` 设计) | `can_access` 单一真值源 + Identity 白名单 + 五执行点 + dev/prod 双模式 | — |
| P2 token+审计 | `dcf16a1` + `59edc48` runbook | `token-add --projects` / `core/audit.py` 越权日志(deny 必记/token allow 留痕/dev 不记) | — |
| 审计 7 项 + blocker | `5ee329b` | token 强制项目上下文(can_access(None)→deny + chat 恒查 + **SSE 回退挪到 ACL 后**)/ `memory_scope_access` / 入口集成测试 / token→prod 日志 / 白名单 validate / webhook 1MB 限流 | SSE 回退顺序 blocker 由审计抓出 |
| P3 远程访问 | `66c1c15` | health 拆分(`/healthz` public + `/platform/status` 鉴权,堵 #4)/ `gateway client-url --base` 远程地址重写 / caddy+TLS runbook | runbook 端口统一为 config 驱动(`client-url` 打印真实值) |
| P5 部分 | (本轮) | **D15** 换机一键 `codev-platform bootstrap`(venv check → serve-mcp start → codegraph link --all → memory init-db,plan 纯函数 + fail-soft)+ onboarding runbook;**B9** 自愈核验:systemd units 早含 `Restart=always`/`RestartSec=3`,本轮补断言锁测;**B10** cross-link 重建已有 `reindex --cross-link`(+ reindex 队列),规则"重建脚本待补"是旧话;**D14** token 注入 `.mcp.json` 由 P2 `client-auth` + P3 `client-url` 覆盖 | D15/B9/B10/D14 闭环;余项见下 |

**关联交付文档**:
- `token-auth-enablement-2026-06-01.md` —— token 模式启用 runbook
- `remote-access-reverse-proxy-2026-06-01.md` —— caddy 反代 + TLS runbook

**未做(留后续轮次)**:
- **#2 memory org/team 完整 RBAC** → M5(PG 角色表);现 interim(token 模式 org/team 默认拒)。
- **B8** WSL 时钟重同步:WSL 内重同步不可靠,暂靠 `wsl --shutdown`,自动 timer 待评估。
- **E16** agent 常驻 systemd:需 fastapi 依赖,暂非常驻。
- **D13** org/user/项目管理 CLI:属 M5(需 PG 角色表)。
- **F18** webhook 自动 link codegraph:暂手动 `link --all`。
- **M5** memory org/team 完整 RBAC(同 #2)。
