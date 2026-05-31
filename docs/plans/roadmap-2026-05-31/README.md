# roadmap-2026-05-31 — 工具栈迭代规划目录

> 本目录 = 2026-05-31 这轮规划的 plan / design 真值源。

## 本轮主题

**多组织服务器加固** —— 平台从"单人能跑"推向"多组织/多人/多项目可安全共享"。核心是补齐多租户**授权隔离**(项目 ACL),其余按"上线必须 / 健壮性 / 体验"分级排期。

## 文件清单

| 文件 | 内容 | 状态 |
|---|---|---|
| [multi-org-server-hardening-2026-05-31.md](multi-org-server-hardening-2026-05-31.md) | 全量现状盘点(A 安全/B 可靠/C 运维/D 体验/E agent/F 索引)+ 优先级路线 + **ACL 项目隔离(模型 C)详细设计** + §六 进度记录 | ✅ A 组(P1 ACL/P2 token+审计/P3 TLS反代+health)闭环;P4/P5 + memory org/team RBAC(M5)留后续 |
| [token-auth-enablement-2026-06-01.md](token-auth-enablement-2026-06-01.md) | Token 模式启用 runbook —— dev passthrough → prod token 完整操作流(项目登记 org / 建 token / 切模式 / 重启服务 / 客户端注入)+ 验证 / 回退 / 审计日志字段说明 | ✅ |
| [remote-access-reverse-proxy-2026-06-01.md](remote-access-reverse-proxy-2026-06-01.md) | Caddy 反代 + TLS runbook(远程多机访问)—— 拓扑 + Caddyfile 路径前缀路由(与 `gateway client-url` 前缀严格一致) + 客户端 client-url/client-auth 操作 + health 探针 + 安全清单 | ✅ |

## 关联

- 上轮:`../roadmap-2026-05-29/`(agent memory 基建:身份 + 权限 + 多作用域,M1-M4 落地)
- 承接:`../roadmap-2026-05-29/memory-permission-model-2026-05-29.md`(memory M5 ACL 接缝;本轮项目 ACL 与其 §3.4 同源对齐)
- 上层规则:`rules/file-discipline.md §4.4` / `rules/agent-provider-architecture.md`(策略接口+registry+零 if-else 铁律)
- 月决策聚合:`docs/log/2026-05.md`
