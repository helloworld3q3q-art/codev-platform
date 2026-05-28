# 运维编排跨平台化:.ps1 → Python CLI(2026-05-28)

> **动机**:整套 dev/运维编排是 .ps1(Windows-only),git hook 还硬编码 `WindowsPowerShell\v1.0\powershell.exe`。移植到 Linux 服务器 / 队友 Mac 全废。但**重活已是 Python**(daemon / 索引器 / cross_link / 健康探针),.ps1 只是 Windows 编排壳。
>
> **目标**:把编排逻辑收进 `codev_platform` 包 + CLI 子命令,一套代码全平台。git hook 调跨平台 CLI。.ps1 退化为可选 Windows 便捷壳(转调 CLI)或删除。
>
> **硬约束(用户指定)**:三仓各自**当前分支拉新分支**编写;**禁止 push**;**可以 commit**;路径**全配置化**(延续 config.json / 相对解析,零硬编码)。

---

## 一、最终形态(CLI 子命令)

| 新 CLI | 替代 .ps1 | 逻辑 |
|---|---|---|
| `codev-platform post-commit` | post-commit.ps1 | git diff-tree → meta.health 模式匹配 scope → spawn reindex |
| `codev-platform reindex [--chroma --codegraph --cross-link] [--force]` | update-local-ai.ps1 | 编排三索引重建(已是 `python -m codev_platform.*` + mvn/pnpm/node) |
| `codev-platform health [--repo R] [--project P] [--mode light\|full]` | ai-health.ps1 | 12+ 项检查(模型/venv/chroma/codegraph/cross_link/daemon/git) |
| `codev-platform dirty-check [--json]` | dirty-index-check.ps1 | 工作树 dirty 文件命中索引范围? |
| `codev-platform install-hooks` | install-git-hooks.ps1 | 写跨平台 hook stub |
| `codev-platform wait-for-reindex` | wait-for-reindex.ps1 | 阻塞等 reindex |

**跨平台 git hook stub**(`.git/hooks/post-commit`):
```sh
#!/bin/sh
exec codev-platform post-commit   # PATH 上的 console script, Win/Mac/Linux 通用
```
(回退 `python -m codev_platform.cli post-commit`)

---

## 二、模块结构(codev-platform 新增)

```
codev_platform/ops/
  _common.py      # 共享: config 读取 / git 仓解析 / meta.health 加载 / scope 模式匹配 / 跨平台 subprocess(吸收 _winexec)
  hooks.py        # post_commit() / dirty_check() / install_hooks()
  reindex.py      # reindex 编排(chroma/codegraph/cross_link)
  health.py       # health 检查(逐项)
cli.py            # 注册子命令(每模块 expose register(subparsers))
```
路径全走 `core.config` + `core.paths`(已有);subprocess 走 `_common` 跨平台解析(Linux/Mac 直跑 mvn/pnpm/node,Windows 走 cmd/c·powershell)。

---

## 三、并行分工(多兄弟)

> cli.py 是冲突点。**我(主)先建 cli.py 注册骨架 + `_common.py`**,兄弟各填自己模块(不动 cli.py 主体,只在自己模块 expose register),最后我集成。

| 兄弟 | 仓 | 任务 | 依赖 |
|---|---|---|---|
| **A** | codev-platform | `ops/reindex.py` + `ops/hooks.py` 的 post_commit/dirty_check(reindex cluster,共享 scope matcher)| `_common` |
| **B** | codev-platform | `ops/health.py`(port ai-health 全部检查,最大块)| `_common` |
| **C** | codev-platform | `ops/hooks.py` 的 install_hooks + 跨平台 hook stub 模板 | `_common` |
| **D** | platform(openclaw)| hook stub 改调 CLI;tools/dev wrapper 改转调 CLI(或删);保留业务专属(pre-push-audit)| 待 A/C 定 CLI 接口 |
| **E** | widget | 同 D(hook + wrapper → CLI)| 同上 |

A/B/C 同仓不同文件,可并行(只 expose register,不抢 cli.py)。D/E 不同仓,可并行,但**依赖 A/C 把 CLI 接口定下来**(分两批:先 A/B/C,验证 CLI 可用,再 D/E)。

---

## 四、分支策略(三仓)

| 仓 | 当前分支 | 新分支 |
|---|---|---|
| codev-platform | main | `feat/xplatform-cli` |
| platform | feat/team-deploy | `feat/xplatform-cli` |
| widget | (当前)| `feat/xplatform-cli` |

各仓 `git checkout -b feat/xplatform-cli`。**只 commit,禁 push**。

---

## 五、跨平台关键点

- subprocess:`_common.run()` 吸收 `_winexec` —— Windows .cmd/.ps1 路由,Linux/Mac 直跑。
- 路径:一律 `pathlib.Path`(自动跨平台分隔符);机器路径来自 config.json。
- daemon/venv:Linux/Mac 上 `chroma_venv` 指各自 venv(config),`cross_link_python` 指各自 python。
- hook stub:`#!/bin/sh` + `exec codev-platform ...`(Mac/Linux git 原生 sh;Windows git-bash sh)。不再硬编码 powershell.exe。
- 模型/GPU:Linux 服务器可 CPU 或 CUDA(config.embed_device);Mac 走 CPU/MPS(后续按需)。

---

## 六、验证

- 每子命令:`codev-platform <cmd>` 在 Windows 跑通(与原 .ps1 输出等价);health all green;post-commit 触发 reindex;install-hooks 装出跨平台 stub。
- 跨平台冒烟:Linux/Mac 上 `pip install -e .[runtime]` + `codev-platform health`(后续真机验)。
- 回归:openclaw 现有 reindex/health 行为不退化。
- .ps1:保留为转调 CLI 的薄壳(Windows 习惯)或标记 deprecated。

---

## 七、风险

- ai-health 46KB 检查逻辑 port 量大(兄弟 B 重点)。
- cli.py 并行冲突 → 用 register() 约定 + 主集成规避。
- 行为等价性:port 后输出/退出码要与 .ps1 一致(git hook / 调用方依赖)。
- 真机跨平台只能 Windows 先验,Linux/Mac 待有环境再冒烟。

---

## 八、执行顺序

1. 主:三仓建分支 + codev-platform `ops/_common.py` + cli.py 注册骨架。
2. 批一并行:兄弟 A(reindex/hooks-core)+ B(health)+ C(install/stub)。
3. 主:集成 cli.py + Windows 验证全子命令。
4. 批二并行:兄弟 D(platform)+ E(widget)hook/wrapper → CLI。
5. 主:三仓 commit(禁 push),各自分支留待用户 review/真机跨平台冒烟。
