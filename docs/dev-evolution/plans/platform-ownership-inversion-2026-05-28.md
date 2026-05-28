# 平台所有权翻正迁移计划（2026-05-28）

> **背景**：codev-platform 名义是平台真值源,但运行时重资产(venv 4.66GB / 模型 launcher / 数据库 / data 目录)物理住在 `D:\WorkSpace\platform`(= openclaw-stock 业务仓)。这是历史包袱 —— 平台运维寄生在业务仓里。
>
> **目标**:codev-platform 全量自立 —— 拥有自己的 venv + 数据 + 运维脚本 + launcher。业务仓只剩 shim,纯消费者。
>
> **决策**:用户 2026-05-28 选「全量自立」。本计划推翻 `CLAUDE.md §八`「venv 不在本仓,本仓 0 deps」的旧设计。

---

## 一、现状清点

| 资产 | 现位置 | 大小/状态 |
|---|---|---|
| 源码真值源 | 已在 codev-platform | ✅ 业务仓已是 shim,无需动 |
| venv(torch cu128 + chromadb + st 等) | `platform/tools/chroma/.venv` | 4.66 GB,**绝对路径烤死,不可移动** |
| launcher | `platform/tools/chroma/platform-docs-mcp.cmd`、`platform/tools/cross_link/cross-link-mcp.cmd` | thin 入口 |
| 运维脚本 | `platform/tools/dev/*.ps1`(ai-health/post-commit/update-local-ai/dirty-check) | |
| 运行时数据 | `platform/data/chroma`(共享多 collection)、`platform/data/codegraph_ext/*/cross_layer.sqlite` | WAL 活跃 |
| codegraph.db | 各仓 `.codegraph/`(第三方 MCP 天然 per-repo) | ✅ 已 per-repo,不用动 |
| config 指针 | `~/.codev-platform/config.json` → `platform_data_dir: D:/WorkSpace/platform/data` | |
| MCP 配置 | 3 仓 `.mcp.json` 均引用 `..\platform\tools\...` | codev-platform / platform / widget |

依赖清单(venv 顶层 10 个)已固化到:
- `pyproject.toml [project.optional-dependencies] runtime`
- `requirements-runtime.txt`(含 torch cu128 index-url)

---

## 二、迁移阶段(P1-P6)

### P1 依赖清单 ✅ 已完成
- pyproject 加 `runtime` optional 组(base deps 仍 `[]`,默认安装不变)
- 建 `requirements-runtime.txt` 锁 torch 2.11.0+cu128

### P2 重建 venv（用户执行）
> venv 不能搬,必须在 codev-platform 重建。`pip install` 是用户手动命令(workflow.md §11)。

```powershell
cd D:\WorkSpace\codev-platform
python -m venv .venv
.venv\Scripts\activate
pip install torch==2.11.0+cu128 --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements-runtime.txt
pip install -e .
```

验证:`.venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available())"` → True

### P3 launcher 搬迁 ✅ 已完成(范围比原计划窄)
> **执行中发现**:运维脚本(ai-health/post-commit/update-local-ai/dirty-index-check 等)**早已是 canonical 在 `codev-platform/scripts/`**(`-Repo` 参数化),业务仓 `tools/dev/*.ps1` 只是 thin wrapper 转发回来。所以"运维脚本搬迁"无需做。`pre-push-audit.ps1` 是 openclaw 业务专属 6 道 gate,**本就该留业务仓**,不搬。
>
> 真正剩下的只有 2 个 launcher:
- ✅ 建 `codev-platform/tools/chroma/platform-docs-mcp.cmd`(venv 指向仓根 `.venv`)
- ✅ 建 `codev-platform/tools/cross_link/cross-link-mcp.cmd`(同上)
- launcher 是 inert 的,P5 翻 .mcp.json 前不生效;它们指向的 `.venv`(P2 建)+ `data`(P4 搬)届时就位

### P4 数据搬迁（需先停 daemon）
1. 停 daemon:`Stop-Process -Id (Get-NetTCPConnection -LocalPort 18083).OwningProcess -Force`
2. 复制 `platform/data/chroma` → `codev-platform/data/chroma`
3. 复制 `platform/data/codegraph_ext` → `codev-platform/data/codegraph_ext`
4. 改 config:`platform_data_dir` → `D:/WorkSpace/codev-platform/data`;`chroma_venv` → 新 venv
5. WAL 活跃,必须 daemon 停后复制,避免半写状态

### P5 改 3 仓 .mcp.json（草稿已就绪）
> 草稿已写为各仓 `.mcp.json.new`（不影响 live 配置）。P2+P4 完成后,apply 即把 .new 覆盖 .mcp.json。

目标路径(各仓 cwd=仓根 解析):
- **codev-platform**:指自己的 `tools\chroma\...` / `tools\cross_link\...`(原指 `..\platform\...`)
- **platform**(业务):指 `..\codev-platform\tools\...`(原指自己 `tools\...`)
- **widget**:指 `..\codev-platform\tools\chroma\...`(原指 `..\platform\...`;无 cross-link)

apply(P2+P4 全绿后):
```powershell
Move-Item D:\WorkSpace\codev-platform\.mcp.json.new D:\WorkSpace\codev-platform\.mcp.json -Force
Move-Item D:\WorkSpace\platform\.mcp.json.new D:\WorkSpace\platform\.mcp.json -Force
Move-Item D:\WorkSpace\codev-platform-widget\.mcp.json.new D:\WorkSpace\codev-platform-widget\.mcp.json -Force
```
apply 后**重启 Claude Code** 才生效(.mcp.json 仅会话启动时读)。

### P6 验证 + 清理 + 更新 CLAUDE.md
1. 重启 daemon → `/health` 全绿 → ai-health → search_docs 跑通(三项目都验)
2. **确认无误后**才删业务仓 `tools/chroma/.venv`、launcher、`data/`(destructive,最后做)
3. 更新 `CLAUDE.md §八`:删「venv 不在本仓 0 deps」,改为新架构说明
4. 更新 `.claude/rules/ai-tools-mcp.md` 故障表里的路径引用

---

## 三、风险 + 回退

| 风险 | 缓解 |
|---|---|
| daemon 停了起不来 | P6 验证通过前**不删**业务仓旧 venv/data,可秒回退(config 指回旧路径 + 旧 launcher) |
| 数据复制损坏 | 用复制不用移动;旧数据保留到 P6 末 |
| torch CUDA 装错成 CPU 版 | requirements-runtime.txt 锁 cu128 index-url |
| 3 仓 .mcp.json 漏改一仓 | P5 逐仓 grep 核对 `..\platform\tools` 清零 |
| 多会话并发撞迁移 | 迁移期间单会话操作,daemon 停期间不开新 Claude 会话 |

**回退总开关**:P6 之前任何阶段失败,把 config.json 的 `platform_data_dir` + `chroma_venv` 指回 `D:/WorkSpace/platform/...`,3 仓 .mcp.json 指回旧 launcher,重启 daemon 即恢复原状。业务仓旧资产在 P6 末才删。

---

## 四、执行顺序口诀

P1(依赖)✅ → P2(用户建 venv)→ P3(搬脚本)→ P4(停 daemon 搬数据)→ P5(改 3 仓配置)→ P6(验证→清理→改文档)。

**destructive(删业务仓资产)只在 P6 末、验证全绿后做。**
