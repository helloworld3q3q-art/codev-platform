# Token 模式启用 Runbook（dev passthrough → prod token）

> 把平台从 dev（passthrough，全放行）切到 prod（token 鉴权 + 项目 ACL）的完整操作流。
> 关联：`multi-org-server-hardening-2026-05-31.md`（ACL 模型 C + P3 TLS）。

## 一、前提（必须已落地）

| 项 | 说明 |
|---|---|
| P1 ACL | `core/acl.py:can_access` 已接入五执行点（chroma / cross-link / codegraph 的 handle_sse + agent chat.py / memory.py） |
| P2 token | `gateway token-add --projects` + 审计日志（`core/audit.py`）已落地 |
| 服务托管 | 平台服务以 systemd 单元运行（`codev-mcp-*` / `codev-reindex` / `codev-webhook`），重启即重读 config |

`can_access` 两闸：闸 1 org（project `org_id` **缺省 = 公开**，否则须 == 身份 `org_id`）；闸 2 白名单（`all_projects` 或 `project_id ∈ identity.projects`）。

## 二、启用步骤（逐条命令）

### 1. 给项目登记 org（缺省即公开，按需收紧）

编辑 `~/.codev-platform/config.json`，为需要隔离的项目设 `org_id`：

```jsonc
{
  "projects": {
    "openclaw-stock":   { "org_id": "acme" },     // 仅 acme 组织可访问
    "codev-platform":   { }                        // 无 org_id = 公开（所有 token 可见）
  }
}
```

> 不设 `org_id` 的项目对所有合法 token 公开；要隔离必须显式登记。

### 2. 建 token（每用户一条，明文只显示一次）

```bash
# 单组织 + 限定项目
codev-platform gateway token-add alice --org acme --projects openclaw-stock,codev-platform
# 全项目（运维 / 平台管理员）
codev-platform gateway token-add ops --org acme --projects '*'
```

输出的明文 token **只打印这一次**（config 只存 `sha256` hash），立即转交对应用户安全保存。
不带 `--projects` 会 WARN：该 token 在 token 模式下无任何项目访问权。
查看 / 撤销：`gateway token-list` / `gateway token-rm <user>`。

### 3. 切模式

```bash
codev-platform gateway mode token
```

写 `config.gateway.auth_mode=token`，`build_authenticator` 下次启动读取。

### 4. 重启平台服务（让 config 重读生效）

```bash
sudo systemctl restart 'codev-mcp-*' codev-reindex codev-webhook
```

### 5. 客户端业务仓注入 token

在每个业务仓执行（写 `.mcp.json` 的 Authorization header → 引用 env 变量）：

```bash
codev-platform gateway client-auth --repo <业务仓路径>   # 默认 cwd
export PLATFORM_TOKEN='<步骤 2 的明文 token>'              # 持久化到用户 shell profile
# 重启 Claude Code 让其重读 .mcp.json + env
```

移除：`gateway client-auth --repo <仓> --remove`。

## 三、验证

| 检查 | 期望 |
|---|---|
| 越权请求（token 无该项目权限） | HTTP **403** |
| 审计日志 `data_root/audit/access.jsonl` | 出现 `"allowed": false` 记录（deny 永远记） |
| 合法请求 | 正常返回 + 审计出现 token allow 留痕（`allowed: true` 非 advisory） |

```bash
# 看最近的拒绝记录
tail -n 20 ~/.codev-platform/data/audit/access.jsonl | grep '"allowed": false'
```

## 四、回退（dev 单机 / 排障）

```bash
codev-platform gateway mode passthrough
sudo systemctl restart 'codev-mcp-*' codev-reindex codev-webhook
```

passthrough 下 ACL 退化为 advisory allow（放行但不污染审计）。

## 五、安全注意

- **token 明文不进 git**：config 只存 `sha256` hash；明文经 env（`PLATFORM_TOKEN`）注入，不写入仓库文件。
- **passthrough 绑非 loopback**：启动期 `auth.py` 会打 WARN（未认证对外开放）。生产严禁 passthrough 监听非 127.0.0.1。
- **跨机必须配 TLS**：token 走 HTTP header，裸 HTTP 跨机即明文裸奔。跨机部署须前置反代终止 TLS，业务仓 `.mcp.json` 切 `https://`，服务仅监听 127.0.0.1 —— 详见 `multi-org-server-hardening-2026-05-31.md §四 P3 A3`。

## 六、审计日志字段说明（`access.jsonl`，每行一条 JSON）

| 字段 | 含义 |
|---|---|
| `ts` | UTC ISO8601 时间戳 |
| `service` | 触发服务（chroma / cross-link / codegraph / agent...） |
| `user_id` | 身份用户标识（token 解析出） |
| `org_id` | 身份所属组织 |
| `via` | 认证途径（token / passthrough） |
| `project_id` | 被访问项目 |
| `allowed` | 判定结果（true / false） |
| `reason` | 判定原因（如 `org mismatch: ...` / 白名单命中） |

> 记录策略：deny 永远记；token allow 记（真授权留痕）；passthrough advisory allow 不记（dev 放行是噪音）。不记任何密钥 / 内容。
