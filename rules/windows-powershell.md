# Windows 批处理 / PowerShell 脚本规则

跑批入口（`*.bat` / `*.ps1` / `*.cmd`）在 **Windows 10/11 + PowerShell 5.1**（系统自带）环境下运行，有几个**坑**必须避免。

## 1. `.ps1` 文件禁止含非 ASCII 字符（含中文 / em dash / 中文标点 / 全角字符）

PowerShell 5.1 默认按 **GBK / ANSI** 解析 `.ps1`。Edit / Write 工具写文件默认是 **UTF-8 无 BOM**。非 ASCII 字符会被错读成乱码，可能在 parser 阶段误成 `}` 触发 **`UnexpectedToken`** 报错（典型现象：报错位置和字符与源代码对不上）。

**规则**：
- ❌ `.ps1` 不要写中文注释 / em dash / 引号 / 全角空格
- ✅ 注释统一英文；分隔线用 `--` 不用 `—`
- 要中文 → 文件**必须保存 UTF-8 with BOM**，或顶部加 `#requires -Version 7`（强制 PS 7+ 用 UTF-8 无 BOM 读）

## 1b. PowerShell 读中文 Markdown / 配置 / 日志必须 `-Encoding UTF8`

PowerShell 5.1 的 `Get-Content` / `Out-File` / `Set-Content` 默认编码是 **GBK / ANSI**(`Default`),读 UTF-8 中文文件会变乱码("锛?" / "鎵??" 这种)。本仓库所有 `.md` / `.json` / `.log` 都是 UTF-8 编码,**必须显式指定**:

```powershell
# ✅ 读 UTF-8 中文文件
Get-Content path\file.md -Encoding UTF8
Get-Content path\config.json -Encoding UTF8 -Raw | ConvertFrom-Json

# ✅ 写 UTF-8 文件(避免给下游工具喂乱码)
Set-Content path\out.txt -Encoding UTF8 -Value $text
Out-File -FilePath path\out.txt -Encoding UTF8 -InputObject $text

# ❌ 不指定编码 → 中文乱码
Get-Content path\file.md
```

**ai-health / dirty-index-check / post-commit 等工具脚本已经统一加 `-Encoding UTF8`**, 不要在 PR 中删除。

**会话级默认值**(可选): 在 `$PROFILE` 加 `$PSDefaultParameterValues['*:Encoding'] = 'utf8'` 后, 当前会话所有 `Get-Content` / `Set-Content` 默认 UTF-8。但这只影响个人开发机, 团队脚本仍**必须显式 `-Encoding UTF8`**(不依赖外部环境)。

## 2. `start "..." cmd /c ...` 窗口闪退

`start "标题" cmd /c <命令>` 命令失败时 cmd 立即关闭，看不到错误。

**规则**：
- ✅ 调试期间用 `cmd /k`（窗口保持打开，按任意键关闭）
- ✅ 稳定后再切回 `cmd /c`（或保留 `/k` 让 helper 末尾 `ReadKey` 兜底）

## 3. Python 子进程实时输出

cmd 窗口要看到 Python 实时日志，必须：
- `$env:PYTHONUNBUFFERED = '1'`（PowerShell）/ `set PYTHONUNBUFFERED=1`（bat）
- `$env:PYTHONIOENCODING = 'utf-8'`
- `chcp 65001 | Out-Null` + `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8`（防中文乱码）
- 调 python 用 `& cmd /c "python `"$script`" 2>&1"`（OS 层合并 stderr，避免 PS 把 stderr 包装成 NativeCommandError 误判失败）

## 4. `.bat` 文件允许 UTF-8

bat 由 cmd.exe 解释，相对宽容；可以含中文，但建议开头加 `chcp 65001 >nul` 切 UTF-8 码页避免输出乱码。

## 5. 路径

Windows 跑批用反斜杠 `\` 或正斜杠 `/` 都可；含空格的路径必须双引号包裹。`%CD%` / `%~dp0` 是 bat 自身机制，PS 里用 `$PSScriptRoot`。

## 6. Claude Code 安全检查友好的命令写法（AI session 必读）

Claude Code CLI 对部分 PowerShell / Bash 模式有**硬编码安全检查**，触发后会跳出确认弹窗，且 `.claude/settings.local.json` 的 allow 规则**无法覆盖**。AI 在本仓库工作时必须主动避开下列模式。

### 四类硬拦截触发器（按出现频率）

| 触发器 | 例子 | 替代写法 |
|---|---|---|
| **复合命令含 `cd <path>;`** | `cd D:\path; mvn test` | `mvn -f D:\path\pom.xml test` / `pnpm --dir D:\path <cmd>` / `git -C D:\path <cmd>` / `python -m pytest D:\path\tests/` |
| **子表达式 `$(...)`** | `Set-Item "env:$($k.Trim())"` | 拆中间变量：`$key = $k.Trim(); Set-Item ('env:' + $key)` |
| **`(pipeline).Property`** | `(Get-Content x \| Measure-Object).Count` | 拆步骤：`$lines = Get-Content x; $cnt = @($lines).Count` |
| **双引号字符串含 `$变量` 插值** | `"progress: $cnt / 110"` | 单引号 + 多参数：`Write-Host 'progress:' $cnt '/ 110'`；或 `-f`：`('progress: {0}' -f $cnt)`；或拼接：`('progress: ' + $cnt)` |

### 两个需要换工具的场景

| 场景 | 不要这么做 | 改用 |
|---|---|---|
| 轮询等待后台任务（Java 启动 / 回测进度） | `for($i=0; ...) { Start-Sleep; Test-NetConnection ... }` 加字符串插值打印进度 | Claude Code 内置 `Monitor` 工具读后台任务 stdout 流，匹配关键字即返回 |
| 读 harness 后台任务 stdout | `Get-Content "...\tasks\xxxx.output" -Tail N` 加 `(pipeline).Count` | `Monitor` 工具直接监听同一文件，零字符串拼接 |

### 复杂命令固化为 `.ps1`

含 `.env` 加载 / 多步环境配置 / 子表达式的高频组合，统一封装到 `python/stock-pipeline/scripts/*.ps1`（参考 `scripts/run_python.ps1`），调用入口纯前缀匹配可被 allow 规则覆盖。`.ps1` 内部使用 `$()` 不再触发检查（检查只看调用方文本）。

### 速查口诀

- 切目录 → 用工具自带 `-f` / `--dir` / `-C` 参数
- 取属性 → 中间变量分两步
- 字符串拼变量 → 单引号 + 多参数，或 `-f`，或 `+`
- 等任务 → `Monitor`

---

## 已有 assertion 兜底

⚪ 纯 why 文档（有 candidate）

目前无自动断言。靠 AI session 自律 + 用户人工 review：
- `.ps1` 非 ASCII 字符问题靠 parser 报错暴露（被动检测）
- Claude Code 硬编码安全检查靠 CLI 弹窗拦截（外部工具，非本仓库 assertion）

盲区（建议未来补）：
- 缺：CI 检查 `*.ps1` 文件无非 ASCII 字符（或必含 UTF-8 BOM）
- 缺：扫"`cd <path>;` 复合命令"反范例（提示改 `-f` / `--dir`）
- 缺：扫 bat 文件 `:failed` hook 完整性（详见 `business-sanity-alerts.md`）
