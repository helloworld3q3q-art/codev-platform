# 工具栈迭代日报 — 2026-06-02

> 真值源 plan：[`refactor-largefile-errorcode-2026-06-01.md`](refactor-largefile-errorcode-2026-06-01.md)（§A.2b 拆分进度 / §B.6 错误码进度）

本日两条主线（大文件拆分 Workstream A + 错误码 Workstream B）均推进收口，并完成 WSL 落盘根治与独立审计修复。全程 `pytest` 435 passed + 真实 WSL daemon `search_docs`（rerank 真打分）兜底。

## 一、大文件拆分（Workstream A，零行为变更）

- **mcp_serve.py 701→485**：systemd 渲染抽到 `mcp_systemd.py`，`mcp_serve.py` 顶部 `import *` re-export，CLI `install-systemd` 零改（`2ad70c3`）。
- **ops/health.py 1119→包**：`health/` 目录化为 `_util` / `_checks` / `_usage` / `__init__`，全部 ≤600，`register()` 入口路径不变（`7faa0af`）。
- **chroma/server.py 1286→568**：按 §A.2b 议定的**无环分层 DAG** 落地——`_config` / `_obslog` / `_stats` / `_helpers` / `_schema` 叶子（只依赖 core）+ `_models` / `_reranker`（→叶子）+ `_tools`，rebound 标量经 `mdl.` / `rr.` 访问。涉及 commit：`3ca9715`（_obslog/_stats/_helpers/_schema）、`364133f`（_tools，过 1000 硬限）、`fedb132`（_config 叶子破 reranker 循环）、`2db9335`（_reranker）、`ef19ce3`（_models，568 达成）。
- **indexer.py 704→591**：文件发现/分类抽到 `_discover.py`（`38f197a`）。
- 结论：四个超限文件**全部 ≤600**，facade re-export 保外部引用不破。

## 二、错误码（Workstream B，additive，error 字符串保留）

- `core/errors.py`——`ErrorCode`（8 类）+ `http_status` 映射 + `PlatformError` + `to_mcp_error` / `to_http_payload`，stdlib-only 叶子（`e135031`）。
- **3 套 MCP 并排 `code`**：chroma / cross_link / codegraph 的 `_err` 分支配机器可读码；**webhook** 6 处错误响应并排 `code`（`707b28d`）。
- 进度记录提交 `10bcb1c`。**agent FastAPI HTTP `code` 字段押后**（§B.6）——可选内部服务，加 code 要改 `HTTPException(detail=...)` 体形状有破 playground 风险，留作有界后续。
- 主错误面（AI 客户端打的 3 套 MCP + webhook）已带机器可读 code。

## 三、WSL 搬 D 盘 + ext4 根治 9p

- `wsl --manage --move` 整个 distro C→D（腾 ~26G）；WSL 升 2.7.3。
- 模型从 `/mnt/d`（9p，加载卡 D 状态 ~80s）迁到 `~/models`（ext4，~18s），冷启动大幅下降。
- 真实 WSL daemon GPU 验证通过（`search_docs` rerank 真打分）。

## 四、独立审计 + 修复

- 派独立 agent 审计本轮拆分/错误码：**无 bug、无回退，435 passed**。
- 修 LOW#1：`_index_config` 叶子破 `indexer ↔ _discover` 循环脆弱（`626abc2`）。
- 修 LOW#3：cross_link 工具 `_err` 补精确 code（`1f85a08`）。
- 修 LOW#4：删 `_tools` 死 `srv` import（`626abc2`）。

## 五、验证基线

全程 `pytest` 435 passed + deeper-mock 烟测（mock `_ensure_project` / `_encode_query` 触 reranker 早返回行抓漏改引用）+ 真实 WSL daemon `search_docs`（rerank 真打分），覆盖 Windows CI 测不到的 GPU 热路径。
