# codev-platform 审计报告 2026-06-03

## 概要

本轮只做项目代码审计、结构分析和测试结果整理，未修改源码或测试实现。项目以 Python 后端为主，CI 入口位于 `.github/workflows/ci.yml`，当前只执行：

```powershell
python -m pytest tests/ -q
```

CI 目标 Python 版本是 `3.13`。本地审计时使用的是系统 Python `3.14.4`，因此最终合入前建议再用 Python 3.13 复跑。

审计开始前，工作区已有未提交改动。本报告只记录现状和测试结果，不判断这些改动来源，也没有回滚或覆盖任何已有改动。

## 审计范围

- 源码目录：`codev_platform/`
- 测试目录：`tests/`
- 主要配置：`pyproject.toml`、`.github/workflows/ci.yml`
- 本地 Python：`Python 3.14.4`
- CI Python：`3.13`
- 收集到的测试数：`669`
- 源码 Python 文件数：`186`
- 测试 Python 文件数：`87`

## 审计开始时的工作区状态

审计开始时 `git status --short` 显示已有修改和新增文件，主要包括：

已修改：

- `codev_platform/ops/reindex.py`
- `codev_platform/plugins/builtin/_stack_scan.py`
- `codev_platform/plugins/registry.py`
- `codev_platform/reindex/runners.py`
- `codev_platform/web/routes/graph.py`
- `codev_platform/webhook/server.py`
- `tests/test_agent_memory_route_acl.py`
- `tests/test_web_graph.py`

新增：

- `codev_platform/plugins/builtin/dotnet.py`
- `codev_platform/plugins/builtin/node.py`
- `codev_platform/plugins/builtin/sql.py`
- `codev_platform/plugins/builtin/vue.py`
- `tests/test_plugins_autodiscovery.py`
- `tests/test_plugins_stack_dotnet.py`
- `tests/test_plugins_stack_node.py`
- `tests/test_plugins_stack_sql.py`
- `tests/test_plugins_stack_vue.py`
- `tests/test_reindex_ingest_stage.py`

## 测试与检查结果

### 已通过

`python -m compileall -q codev_platform tests`

- 结果：通过
- 说明：未发现 Python 语法或编译阶段的阻塞问题。

`python -m pytest tests/ --collect-only -q`

- 结果：通过
- 收集测试数：`669`
- 警告：FastAPI/TestClient 相关依赖出现 Starlette 弃用警告。

`python -m pytest tests/ -q -k "not plugins"`

- 结果：`593 passed, 5 skipped, 72 deselected`
- 耗时：`14.39s`
- 说明：排除插件相关测试后，主体测试稳定通过。

`python -m pytest tests/test_plugins_registry.py tests/test_plugins_builtin_crosslink.py tests/test_plugins_autodiscovery.py -q`

- 结果：`22 passed`

`python -m pytest tests/test_plugins_stack_node.py tests/test_plugins_stack_sql.py tests/test_plugins_stack_dotnet.py tests/test_plugins_stack_vue.py -q`

- 结果：`38 passed`

`tests/test_plugins_stack.py` 中的基础用例：

- 结果：`9 passed`
- 覆盖内容：React/FastAPI detect、合成仓库 analyze、插件注册。

`python -m pytest tests/test_plugins_stack.py::test_fastapi_analyze_nonempty_on_repo -q`

- 结果：通过
- 耗时：`39.07s`
- 说明：功能正确，但单个单测耗时过高。

### 超时或未完整通过

`python -m pytest tests/ -q`

- 结果：约 `124s` 后超时
- 观察到的原因：完整测试进入插件栈测试后，会触发对当前整个仓库的插件分析，耗时过高。

`python -m pytest tests/test_plugins_stack.py -q -vv`

- 结果：约 `124s` 后超时

`python -m pytest tests/test_plugins_stack.py::test_react_analyze_nonempty_on_repo -q`

- 结果：约 `60s` 后超时

## 主要问题

### P1：插件栈扫描会进入大目录，导致全量测试超时

位置：`codev_platform/plugins/builtin/_stack_scan.py`

当前栈扫描工具使用 `Path.rglob("*")` 收集文件，并且 React、Vue、Node 的 detect 逻辑也直接使用 `repo.rglob(...)`。代码虽然会在拿到路径后检查 `_SKIP_DIRS`，但此时 `rglob` 已经递归进入 `.venv`、`build`、`dist`、`.codegraph`、`data` 等大目录。

本地仓库正好存在这些目录，因此对 `REPO_ROOT` 做插件分析时非常慢。

已观察到的影响：

- `test_fastapi_analyze_nonempty_on_repo` 能通过，但单测耗时 `39.07s`。
- `test_react_analyze_nonempty_on_repo` 超过 `60s` 仍未完成。
- 完整 `pytest` 超过 `124s` 后超时。
- 超时后曾残留插件扫描相关 Python 子进程，需要手动清理。

建议修复：

- 将 `_stack_scan.py` 中的 `Path.rglob("*")` 替换为可剪枝的遍历方式，例如 `os.walk`，在进入子目录前过滤 `dirnames[:]`。
- React、Vue、Node 的 `package.json` 探测也复用同一套跳过目录规则。
- 增加回归测试：在应跳过目录中放大量匹配文件，验证扫描不会访问这些目录。

## 其他发现

### P2：CI 只有 pytest，没有 lint、类型检查或超时保护

位置：`.github/workflows/ci.yml`

CI 当前只安装包并运行测试：

```powershell
python -m pytest tests/ -q
```

没有配置 lint、类型检查、导入顺序检查或 pytest 超时机制。像这次插件扫描超时的问题，只能在完整测试挂住后暴露。

建议：

- 给 pytest 增加超时保护。
- 将插件全仓分析类测试与普通单元测试拆分。
- 后续可逐步加入 lint 或静态检查。

### P2：本地 `.venv` 缺少 pytest

执行 `.venv\Scripts\python.exe -m pytest --version` 时失败：

```text
No module named pytest
```

系统 Python 可用：

```text
Python 3.14.4
pytest 9.0.3
```

但 CI 使用 Python 3.13，所以当前测试结果有参考价值，但不是最终环境确认。

建议：

- 在项目虚拟环境安装开发依赖：`pip install -e .[dev]`
- 最终验证使用 Python 3.13。

### P3：README 和 pyproject 中的中文在当前终端显示乱码

涉及文件：

- `README.md`
- `pyproject.toml`

审计读取时，中文内容显示为乱码。可能是文件编码问题，也可能是 PowerShell 输出编码问题。无论原因如何，这会影响维护者阅读项目说明和依赖说明。

建议：

- 确认文件实际编码是否为 UTF-8。
- 确认 PowerShell 输出编码和编辑器编码设置。

### P3：FastAPI/TestClient 依赖有弃用警告

测试收集和执行时观察到：

```text
StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
```

建议：

- 跟踪 FastAPI、Starlette、TestClient、httpx/httpx2 的版本兼容。
- 必要时固定依赖版本或跟进迁移。

## 后续审计重点

- `codev_platform/web/`：文件数量最多，路由、认证、会话、安全边界应继续重点审计。
- `codev_platform/agent/`：记忆、RBAC、Provider 翻译、工具调用循环覆盖面较大，但仍建议做集成边界审查。
- `codev_platform/plugins/`：插件隔离单测表现正常，但全仓扫描性能需要优先加固。
- `codev_platform/reindex/` 和 `codev_platform/ops/`：涉及 subprocess 和长运行 worker，建议明确超时、清理和失败降级策略。

## 建议下一步

1. 优先修复 `_stack_scan.py` 的目录剪枝遍历问题，保持插件输出语义不变。
2. 修复后复跑：
   - `python -m pytest tests/test_plugins_stack.py -q`
   - `python -m pytest tests/ -q`
3. 使用 Python 3.13 做最终验证。
4. 增加跳过目录的性能回归测试。
5. 考虑把分析真实 `REPO_ROOT` 的插件测试标记为集成测试，避免拖慢普通单元测试。

---

## 整改状态（2026-06-03 收尾）

逐项核到代码底层 / 实测后的真实状态：

| 项 | 状态 | 依据 |
|---|---|---|
| **P1** `_stack_scan.py` rglob 进大目录 | ✅ **已修+测** | 全面改用 `_walk_pruned`（`os.walk` + `dirnames[:]` 原地剪枝 `_SKIP_DIRS` + 隐藏目录），所有入口（`_iter_files`/`_iter_named`/`_has_file_with_suffix`）共用，无 `rglob`。回归测试 `test_sql_detect_skips_build_dirs`（node_modules 内 .sql 不命中）。**实测全量 `pytest tests/` 25s 791 passed**（审计时 124s 超时）—— 性能问题已解 |
| **P2** CI 无超时保护 | ✅ **已修** | `pytest-timeout` 进 dev extras；CI `--timeout=120 --timeout-method=thread`（thread 法跨 windows/ubuntu/macos 一致）。实测带 flag 全量 791 passed 23.93s，无用例触顶 |
| **P2** 本地 `.venv` 缺 pytest | ◻ **环境/运维** | `.venv` 是 chroma 重 ML 环境；纯逻辑测试走 system python 3.14（pytest 9.0.3）或 `.venv` 装 `.[dev]`。非代码债。运维主线走 WSL `.venv`（见 [[codev-platform-ops-via-wsl]]）|
| **P2** CI 加 lint/类型检查 | ⏳ **更大改造，另立** | 仅"建议"非阻塞。项目暂无 lint 基线，引入 ruff 会一次性暴露大量历史项，需单独一轮。不在本次范围（不假称完成）|
| **P3** README/pyproject 中文乱码 | ◻ **误报** | 字节探测：两文件均无 BOM UTF-8，`python` 读取正常解码（`[build-system]` 清晰）。审计看到的乱码是 PowerShell 终端显示编码问题（审计自身已存疑），**文件无问题** |
| **P3** Starlette/TestClient 弃用警告 | ⏳ **依赖跟踪** | 上游 `httpx`→`httpx2` 迁移警告，非本仓代码问题。待 FastAPI/Starlette 生态稳定后跟进，当前仅 1 条 warning 不影响测试 |

**结论**：两个 P1/P2 实质代码项（栈扫描剪枝 + CI 超时）已修并测；其余为环境/误报/依赖跟踪/更大改造另立。本审计代码侧闭环。

