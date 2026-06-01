# 2026-06-01 审计整改状态

> 对应审计报告 `codev-platform-audit-report-2026-06-01.md`(三轮审计:代码结构 / 文档边界 / 发布交付·运行时·安全·部署)。
> 本文件记录每项发现的整改结果与 commit。整改全程在 WSL 真值源仓 `~/work/codev-platform`,推 Gitea `dev`。测试基线 306 → 341 passed。

## P0 / P1(发布前必须修)

| # | 发现 | 整改 | commit |
|---|---|---|---|
| 1 | chroma server 顶层 `import chromadb` + import 期 `sys.exit`,轻量环境/安全测试 import 崩(唯一失败测试) | chromadb 移入 `_get_client` lazy;`ProjectIdError → PROJECT_ID=None`(同 cross_link/codegraph)。实测无 chromadb 也能 import | `8397dde` |
| 3 | platform-docs `/healthz` 404(P3 前旧 daemon),编排器误判 DOWN | `serve-mcp` 探针 `/healthz` 失败 fallback `/health`(仅看 status code),兼容旧 daemon | `8397dde` |
| 4 | chroma reload 用全局 `.last_build.json`,一项目重建波及他项 | `_maybe_reload_project` 优先读 `.last_build.<pid>.json`,全局仅 fallback | `8397dde` |
| 5 | health cross-link 路径硬编码本仓 data,override 后误报缺失 | 改用 `cross_link_db_path(project_id)`,尊重 `PLATFORM_DATA_DIR` | `8397dde` |
| 6 | 生产认证默认 passthrough,`warn_if_insecure` 仅告警非 fail-fast | `deployment.mode=prod` 且非 token(或远程 url 非 token)→ `deploy_policy_error` + agent 启动 `sys.exit(2)` | `8397dde` |
| 10 | cross-link/codegraph SSE 非法 project_id 裸 `return None` → Starlette TypeError | 三 server `handle_sse` 非法 pid 返 `JSONResponse(400)` | `8397dde` |

## P2(产品化前应修)

| # | 发现 | 整改 | commit |
|---|---|---|---|
| 2 | wheel 缺 rules/skills/config.example,普通 pip 装后 sync-rules 失败 | `rules/`+`skills/` relocate 进 `codev_platform/resources/`,`importlib.resources` 定位 + `package-data` 随 wheel 走(实测 /tmp 下 sync-rules 通)。config.example 代码不读,留仓根 | `01ab0c5` |
| 7 | `~/.codev-platform/config.json` 含明文 API key | 加 `config doctor/show --redact`(`redact_config` 深度掩码 api_key/password/secret/token/dsn)。**key 轮换是用户操作** | `8397dde` |
| 9 | 注释/docstring mojibake | **WSL 真值源经核查无 mojibake**(0 替换字符 / 0 GBK 乱码序列);属 Windows 克隆 CRLF/编码翻车历史产物 → Windows 克隆 `git pull` 取干净源即可 | — |

## 余项

| # | 发现 | 状态 |
|---|---|---|
| 8 | Docker 部署未验证 | 🔒 本机无 docker,`docker compose up` 闭环验不了 → 待有 docker 环境 |

## 顺带补强(审计周边)

- B8 时钟重同步 systemd timer(WSL 睡眠后漂移=chroma flap 根因,best-effort)、E16 agent 常驻 unit、F18 codegraph reindex 自动 ensure-link、C12 `codev-platform logs` 集中日志 —— `8a3c878`。

## 结论

审计 10 项:**9 项已整改**(#1/#3/#4/#5/#6/#7/#10 修复,#2 relocate,#9 真值源本就干净),#8 待 docker 环境。测试 341 passed / 2 skipped。`#7 的 API key 轮换`需用户执行。
