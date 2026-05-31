# 换机 onboarding —— 一键 bootstrap runbook (D15)

> **定位**:换生产服务器 / 重 clone 业务仓 / 新机器初装后,把平台跑起来的**最短路径**。
> 把"换机后要手动敲的几条命令"压成一个有序、幂等、fail-soft 的编排入口 `codev-platform bootstrap`。
>
> 关联:`persistence-backup-2026-06-01.md`(PG/备份)/ `token-auth-enablement-2026-06-01.md`(token 模式)/ `remote-access-reverse-proxy-2026-06-01.md`(反代)。

---

## 一、前提清单(bootstrap 前必须就位)

| 项 | 说明 |
|---|---|
| clone 平台仓 | `codev-platform` 本体仓 clone 到本机(如 `~/work/codev-platform`) |
| clone 业务仓 | 每个要接入的业务仓 clone 到本机(路径写进 config 的 `projects.<pid>.repo_path`) |
| `~/.codev-platform/config.json` 已配 | 用户级配置,**换机器只改这里,代码不动**(字段全集见仓根 `config.example.json`) |
| models 路径 | embedding/reranker 模型(如 `D:/models/Qwen3-Embedding-0.6B`)落到本机,路径与 config 一致 |

### config.json 机器相关必改项

换机后这几项**必然变**(绝对路径 / 本机 DSN),逐项核对:

| config 路径 | 含义 | 换机注意 |
|---|---|---|
| `data.platform_data_dir` | chroma collection + cross_layer.sqlite + codegraph 数据基目录 | 改成本机实际数据盘路径 |
| `runtime.chroma_venv` | chroma daemon / indexer 用的 venv(含 python + mcp-proxy) | 指向本机平台 `.venv` 绝对路径 |
| `projects.<pid>.repo_path` | 每个项目业务仓本机路径 | 每个项目逐个核对,clone 到哪写哪 |
| `memory.pg_dsn` | memory 持久化 PG 独立库 DSN(含密码) | 启用 memory 才填;不启用留 `null`(会话仅内存) |

> 安全红线:`memory.pg_dsn` 含密码,真值只写本机 config 或 env(`CODEV_PLATFORM_MEMORY_DSN`),**绝不进仓**。

---

## 二、一键 bootstrap

```bash
codev-platform bootstrap --dry-run   # 先看将执行哪些步骤(只打印不执行)
codev-platform bootstrap             # 正式执行(fail-soft:某步失败不中断后续,末尾汇总)
```

`bootstrap` 按 config 条件产出**有序步骤**,逐步说明它做什么:

| # | 步骤 | kind | 做什么 | 条件 |
|---|---|---|---|---|
| ① | `venv` | check | 当前解释器能否 `import codev_platform` + 仓根 `.venv` 是否在(**只体检,绝不自动 pip**;缺则提示用 `requirements-runtime.txt` 重建) | 恒在,最前 |
| ② | `serve-mcp` | cmd | `serve-mcp start`:拉起平台 4 个 MCP 端点(chroma/cross-link/codegraph,chroma 预热 ~30-60s) | 恒在 |
| ③ | `codegraph-link` | cmd | `codegraph link --all`:为 config 已登记项目重建 junction 联接(幂等) | 仅当 `config.projects` 非空 |
| ④ | `memory-init-db` | cmd / guide | `memory init-db`:幂等建 memory PG schema 表(`CREATE TABLE IF NOT EXISTS`,**不建 database**) | 仅当 `memory.pg_dsn` 已配;为空则降级为 guide(跳过) |

> 设计:plan 纯函数只读 config 推导步骤,不在 import / plan 期起进程或建库;执行期 fail-soft,某步 rc≠0 记录后继续,末尾返回非 0 并列未通过项。

---

## 三、bootstrap 不自动做的(仍需人工)

这几件涉及重依赖 / 超级用户权限 / 外部设施,bootstrap **故意不碰**,按对应 runbook 手动做:

| 事项 | 为什么不自动 | 见哪 |
|---|---|---|
| ① venv 重建(`requirements-runtime.txt`,4.66GB 重 ML 依赖) | bootstrap 绝不自动 pip;装重依赖是显式动作 | `CLAUDE.md §八` |
| ② 装 psycopg(memory PG 用) | 用户手动装进平台 `.venv`:`.venv/bin/pip install -e '.[agent]'`(agent extra 含 `psycopg[binary,pool]`) | `persistence-backup-2026-06-01.md` §① |
| ③ 超级用户 `CREATE DATABASE codev_platform_memory` | `memory init-db` 只建表不建库;建库需 PG 超级用户 | `persistence-backup-2026-06-01.md` §② |
| ④ token 模式启用 / 反代 + TLS | 多组织 / 远程访问才需要;dev passthrough 默认不需 | `token-auth-enablement-2026-06-01.md` / `remote-access-reverse-proxy-2026-06-01.md` |

---

## 四、验证(bootstrap 后逐项绿)

```bash
codev-platform health --all      # 平台全局视图:所有项目 x 三库 + 记忆
codev-platform serve-mcp status  # 4 个 MCP 端点全 OK
codev-platform memory doctor     # 启用 memory PG 时:dsn 已配 / psycopg 已装 / 连库成功 / 3 张表齐全,退 0 = 全绿
```

未启用 memory PG 则跳过 `memory doctor`(bootstrap 第 ④ 步已 guide 跳过)。

---

## 五、关联 runbook

| runbook | 何时配套看 |
|---|---|
| `persistence-backup-2026-06-01.md` | 启用 memory PG 持久化 + 定时备份/恢复 |
| `token-auth-enablement-2026-06-01.md` | 多组织 / 多人,从 dev passthrough 切 prod token 模式 |
| `remote-access-reverse-proxy-2026-06-01.md` | 远程多机访问,Caddy 反代 + TLS + 客户端 client-url |
