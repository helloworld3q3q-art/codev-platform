# PG 持久化启用 + 备份/恢复 runbook (P4)

> 承接 `multi-org-server-hardening-2026-05-31.md` §P4。把"单人内存态"推向"多人/生产持久化 + 可恢复"。
> 命令名/flag 对照 `codev_platform/ops/memory_db.py`(memory) + `ops/backup.py`(backup) 真值。

---

## 一、PG 持久化 (B6)

### 为什么

`agent.session_backend` 默认 `memory` —— 会话 + memory 仅进程内,**重启即丢**。多人 / 生产必须切 PG 持久化:会话历史、memory_entries 落库长存,多实例共享。

### 启用步骤

**① 装 psycopg(用户手动,装进平台 .venv)**

```bash
# 绝不由 AI 自动 pip(rules §11)。用户在平台仓手动:
.venv/bin/pip install -e '.[agent]'   # agent extra 含 psycopg[binary,pool]>=3.2
```

**② 超级用户建库**(独立 database,非业务库)

```sql
-- 以 postgres 超级用户连上 PG 后执行:
CREATE ROLE codev_platform LOGIN PASSWORD '<强密码-走-env-不进-git>';
CREATE DATABASE codev_platform_memory OWNER codev_platform;
-- 权限收口: 该 role 只对本库有权, 不碰任何业务库。
```

> 🔴 红线:memory 必须用**独立 database**,严禁复用业务库 DSN —— 见 `../roadmap-2026-05-29/memory-permission-model-2026-05-29.md §3.2b`。`memory init-db` 只建表(`CREATE TABLE IF NOT EXISTS`),**绝不建 database**,所以建库这步必须超级用户手动做。

**③ 配 dsn**(密码走 env,不进 git)

本机 `~/.codev-platform/config.json`:

```json
{ "memory": { "pg_dsn": "postgresql://example.invalid/database", "pg_dsn_read": null } }
```

密码不写进 config,走环境变量 `CODEV_PLATFORM_MEMORY_DSN`(优先级高于 config),或在 dsn 里用 PG 的 `~/.pgpass`。`pg_dsn_read` 留 null = 读写同库(只读副本预留)。

**④ 幂等建表**

```bash
codev-platform memory init-db   # 复用 session_pg / memory_store_pg 现成 DDL, 建 agent_sessions / agent_messages / memory_entries
```

**⑤ 体检**

```bash
codev-platform memory doctor    # 核 dsn 已配 / psycopg 已装 / 连库成功 / 3 张表齐全; 退 0 = 全绿
```

**⑥ 切后端**

`config.json` 的 `agent.session_backend` 由 `memory` 改 `pg`:

```json
{ "agent": { "session_backend": "pg" } }
```

**⑦ 重启 agent 服务**让新后端 + dsn 生效。

---

## 二、备份 / 恢复 (B7)

### 备份命令

```bash
codev-platform backup [--out DIR] [--keep N] [--dry-run] [--gitea DIR]
```

| flag | 默认 | 说明 |
|---|---|---|
| `--out` | `config backup.out_dir` 或 `data_root/../backups` | 输出根目录 |
| `--keep` | `7` | 保留最新 N 份, 其余删; `<=0` 不清理 |
| `--dry-run` | off | 只打印 plan 不执行 |
| `--gitea` | `config backup.gitea_dir` | 额外备份的 gitea 数据目录 |

每次生成时间戳目录 `codev-backup-YYYYMMDD-HHMMSS/`,内含:
- `memory-pg.sql` —— `pg_dump` of `memory.pg_dsn`(dsn 为空则跳过,不进 plan)
- `data-chroma.tar.gz` / `data-cross_layer.sqlite` / `data-codegraph_ext.tar.gz` / `data-audit.tar.gz` —— `data.platform_data_dir` 下存在的子项
- `gitea.tar.gz` —— 若配了 gitea 目录
- `manifest.json` —— plan + 每项 ok/error 结果

### 定时备份(每日,保留 7 份)

**systemd timer** (`/etc/systemd/system/codev-backup.{service,timer}`):

```ini
# codev-backup.service
[Service]
Type=oneshot
Environment=CODEV_PLATFORM_MEMORY_DSN=postgresql://example.invalid/database:<pw>@internal.example.invalid/codev_platform_memory
ExecStart=/home/user/project backup --keep 7

# codev-backup.timer
[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true
[Install]
WantedBy=timers.target
```

`sudo systemctl enable --now codev-backup.timer`

**cron 等价**:

```cron
0 3 * * * CODEV_PLATFORM_MEMORY_DSN='postgresql://example.invalid/database...' /home/user/project backup --keep 7
```

### 恢复

> 🔴 恢复前**先停 agent / daemon 服务**,防写冲突(回灌期间不能有进程在写库 / 写 data_root)。

**1. PG memory 回灌**:

```bash
psql --dbname codev_platform_memory < codev-backup-YYYYMMDD-HHMMSS/memory-pg.sql
```

**2. data_root 子目录解压回原位**(替换前先 `mv` 旧目录留底):

```bash
DATA=<data.platform_data_dir>
tar -xzf codev-backup-.../data-chroma.tar.gz -C "$DATA"          # 还原 chroma/
cp codev-backup-.../data-cross_layer.sqlite "$DATA/cross_layer.sqlite"
tar -xzf codev-backup-.../data-codegraph_ext.tar.gz -C "$DATA"
tar -xzf codev-backup-.../data-audit.tar.gz -C "$DATA"
```

**3. 重启服务**,跑 `codev-platform memory doctor` + `serve-mcp status` 验证。

### 一致性权衡

`chroma` / `cross_layer.sqlite` / `codegraph_ext` 的 tar/copy 是**热备**:若备份时 daemon 正在写,可能拿到弱一致快照。生产建议在 daemon 静默期(如凌晨 timer 时段,无活跃会话)跑;或接受热备一致性弱(这些是可从源码 / 业务库重建的索引产物,非唯一真值源,弱一致可容忍)。PG 走 `pg_dump` 本身是事务一致快照,无此问题。
