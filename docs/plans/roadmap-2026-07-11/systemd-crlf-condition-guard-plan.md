# systemd 未知条件兼容与防绕过实施计划

> **状态（2026-07-20）：** Task 1、2 与 Task 3 本地收口已完成；正式 WSL 重新部署及四库验收
> 由 `roadmap-2026-07-19` runtime-generation Integration 10–13 承接，不再作为独立待办计数。
>
> **供自动化执行者使用：REQUIRED SUB-SKILL：** 使用 `superpowers:executing-plans`
> 按 TDD 顺序逐项执行，并在提交前使用 `superpowers:verification-before-completion` 完成证据验证。

**目标：** 在不放宽已知 systemd 条件原像的前提下，安全接受未知 root 可信 drop-in 的
完整 CRLF 行尾，并封堵 BOM 隐藏 `Condition*` / `Assert*` 的既有绕过路径。

**架构：** 新增一个不读文件、不调用 systemd 的纯字节扫描器，集中维护未知 drop-in 的
语法边界；通用部署门禁和 CodeGraph 永久维护门禁只负责把扫描结果翻译为各自领域异常。
文件所有权、模式、链接和大小仍由现有可信快照读取器证明，已知条件仍逐字节精确比较。

**技术栈：** Python 3.12、pytest、Ruff、systemd 255、WSL2。

## 全局约束

- 只接受完整 `\r\n`，规范化后仍存在的裸 `\r` 必须失败关闭。
- 任意反斜杠、NUL、非法 UTF-8 或 UTF-8 BOM 都必须失败关闭。
- 物理行只按 `b"\n"` 拆分，键名只按 systemd 的 ASCII 空格和 Tab 去边界。
- 未知 `Condition*` / `Assert*` 在 LF、CRLF 或混合合法行尾下都必须拒绝。
- `required` / `allowed` 原像继续按原始 bytes 精确匹配，不接受 CRLF 漂移。
- 不重写 `/etc/systemd/system` 旧文件；现有失败回执保留审计。
- 所有沟通、注释和文档使用中文；不推送 GitHub，只推送 `origin/dev`。

---

### 任务一：建立共享纯字节扫描器

**文件：**

- 新建：`codev_platform/ops/systemd_condition_syntax.py`
- 新建：`tests/test_systemd_condition_syntax.py`

**接口：**

- 产出：`verify_no_unknown_condition_directives(content: bytes) -> None`
- 产出：`SystemdConditionSyntaxError` 与 `UnknownSystemdConditionDirectiveError`
- 依赖：无文件系统、无 subprocess、无领域模块。

- [ ] **步骤 1：先写失败测试**

```python
@pytest.mark.parametrize(
    "content",
    (
        b"[Service]\r\nEnvironment=SAFE=1\r\n",
        b"[Service]\nEnvironment=SAFE=1\r\n",
    ),
)
def test_未知服务配置接受完整CRLF(content: bytes) -> None:
    verify_no_unknown_condition_directives(content)


@pytest.mark.parametrize(
    "content",
    (
        b"[Unit]\r\nConditionPathExists=/unsafe\r\n",
        b"[Unit]\r\nAssertPathExists=/unsafe\r\n",
    ),
)
def test_CRLF不能隐藏未知条件(content: bytes) -> None:
    with pytest.raises(UnknownSystemdConditionDirectiveError):
        verify_no_unknown_condition_directives(content)


@pytest.mark.parametrize(
    "content",
    (
        b"[Service]\rEnvironment=SAFE=1\n",
        b"[Unit]\\\r\nConditionPathExists=/unsafe\r\n",
        b"[Service]\nEnvironment=SAFE=1\x00\n",
        b"[Service]\nEnvironment=\xff\n",
        b"[Unit]\n\xef\xbb\xbfConditionPathExists=/unsafe\n",
    ),
)
def test_不可证明的物理语法失败关闭(content: bytes) -> None:
    with pytest.raises(SystemdConditionSyntaxError):
        verify_no_unknown_condition_directives(content)
```

- [ ] **步骤 2：运行红灯测试**

```powershell
python -m pytest tests/test_systemd_condition_syntax.py -q
```

预期：测试收集因共享模块尚不存在而失败。

- [ ] **步骤 3：实现最小共享扫描器**

```python
class SystemdConditionSyntaxError(RuntimeError):
    """未知 systemd drop-in 的条件语法无法安全证明。"""


class UnknownSystemdConditionDirectiveError(SystemdConditionSyntaxError):
    """未知 systemd drop-in 声明或重置了条件。"""


def verify_no_unknown_condition_directives(content: bytes) -> None:
    if type(content) is not bytes:
        raise SystemdConditionSyntaxError("systemd drop-in 内容类型无效")
    normalized = content.replace(b"\r\n", b"\n")
    try:
        text = normalized.decode("utf-8", "strict")
    except UnicodeError:
        raise SystemdConditionSyntaxError("systemd drop-in 不是严格 UTF-8") from None
    if b"\r" in normalized or b"\\" in normalized or b"\x00" in normalized or "\ufeff" in text:
        raise SystemdConditionSyntaxError("systemd drop-in 物理语法不可证明")
    for line in normalized.split(b"\n"):
        stripped = line.strip(b" \t")
        if not stripped or stripped.startswith((b"#", b";", b"[")):
            continue
        key, separator, _value = stripped.partition(b"=")
        if separator and key.strip(b" \t").startswith((b"Condition", b"Assert")):
            raise UnknownSystemdConditionDirectiveError("systemd drop-in 含未知条件或重置")
```

- [ ] **步骤 4：运行绿灯测试与 Ruff**

```powershell
python -m pytest tests/test_systemd_condition_syntax.py -q
python -m ruff check codev_platform/ops/systemd_condition_syntax.py tests/test_systemd_condition_syntax.py
python -m ruff format --check codev_platform/ops/systemd_condition_syntax.py tests/test_systemd_condition_syntax.py
```

预期：全部通过。

### 任务二：让两个领域门禁共享唯一策略

**文件：**

- 修改：`codev_platform/ops/systemd_condition_guard.py`
- 修改：`codev_platform/ops/reindex_codegraph_maintenance_guard.py`
- 修改：`tests/test_systemd_condition_guard.py`
- 修改：`tests/test_reindex_codegraph_maintenance_guard.py`

**接口：**

- 消费：`verify_no_unknown_condition_directives(content: bytes) -> None`
- 保留：两个领域模块现有公开接口与中文异常语义。

- [ ] **步骤 1：为两个调用方补红灯集成测试**

```python
def test_未知CRLF服务配置不会误拒绝() -> None:
    service = Path("/etc/systemd/system/codev-mcp-codegraph.service.d/20-cpu.conf")
    snapshots = {
        _DEPLOYMENT.path: _snapshot(_DEPLOYMENT.content),
        service: _snapshot(b"[Service]\r\nEnvironment=SAFE=1\r\n"),
    }

    verify_systemd_condition_guard(
        _UNIT,
        required=_DEPLOYMENT,
        platform_name="linux",
        reader=lambda path, **_kwargs: snapshots.get(path),
        command_runner=_runner((_DEPLOYMENT.path, service)),
    )


def test_已知条件仅换成CRLF仍按原像漂移拒绝() -> None:
    snapshots = {
        _DEPLOYMENT.path: _snapshot(_DEPLOYMENT.content),
        _CODEGRAPH.path: _snapshot(_CODEGRAPH.content.replace(b"\n", b"\r\n")),
    }

    with pytest.raises(SystemdConditionGuardError, match="固定条件不受信任"):
        verify_systemd_condition_guard(
            _UNIT,
            required=_DEPLOYMENT,
            allowed=(_CODEGRAPH,),
            platform_name="linux",
            reader=lambda path, **_kwargs: snapshots.get(path),
            command_runner=_runner((_DEPLOYMENT.path, _CODEGRAPH.path)),
        )


def test_CodeGraph维护条件接受无条件的CRLF旧dropin() -> None:
    from codev_platform.ops import reindex_codegraph_maintenance_guard as module

    legacy = Path("/etc/systemd/system/codev-mcp-codegraph.service.d/20-cpu.conf")
    snapshots = {
        legacy: _snapshot(b"[Service]\r\nEnvironment=SAFE=1\r\n"),
        module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH: _snapshot(
            module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_CONTENT
        ),
    }

    module.verify_codegraph_maintenance_guard(
        platform_name="linux",
        reader=lambda path, **_kwargs: snapshots.get(path),
        command_runner=lambda *_args, **_kwargs: _ok(
            stdout=(
                f"DropInPaths={legacy.as_posix()} "
                f"{module.CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH.as_posix()}\n"
            )
        ),
    )
```

- [ ] **步骤 2：运行红灯测试**

```powershell
python -m pytest tests/test_systemd_condition_guard.py tests/test_reindex_codegraph_maintenance_guard.py -q
```

预期：两个安全 CRLF 场景仍被旧扫描器拒绝。

- [ ] **步骤 3：替换重复扫描逻辑**

两个领域包装器导入共享函数，并分别把共享异常翻译为原有领域异常：

```python
from codev_platform.ops.systemd_condition_syntax import (
    SystemdConditionSyntaxError,
    UnknownSystemdConditionDirectiveError,
    verify_no_unknown_condition_directives,
)


def _reject_unknown_condition(content: bytes) -> None:
    try:
        verify_no_unknown_condition_directives(content)
    except UnknownSystemdConditionDirectiveError:
        raise SystemdConditionGuardError("systemd drop-in 含未知条件或重置") from None
    except SystemdConditionSyntaxError:
        raise SystemdConditionGuardError("systemd 未知条件语法无法证明") from None


def _reject_condition_directives(content: bytes) -> None:
    try:
        verify_no_unknown_condition_directives(content)
    except UnknownSystemdConditionDirectiveError:
        raise CodegraphMaintenanceGuardError("CodeGraph drop-in 含未知条件或重置") from None
    except SystemdConditionSyntaxError:
        raise CodegraphMaintenanceGuardError("CodeGraph drop-in 条件语法无法证明") from None
```

删除两个包装器内重复的 UTF-8、CR、续行和条件键扫描代码，不改变快照或已知原像比较。

- [ ] **步骤 4：运行聚焦回归**

```powershell
python -m pytest tests/test_systemd_condition_syntax.py tests/test_systemd_condition_guard.py tests/test_reindex_codegraph_maintenance_guard.py tests/test_runtime_deployment_guard.py -q
```

预期：全部通过。

### 任务三：记录、验证、提交并恢复部署

**文件：**

- 修改：`docs/plans/roadmap-2026-07-11/daily-summary-2026-07-18.md`

- [ ] **步骤 1：记录真实故障链和决策**

记录 `release_staged` 已通过、`maintenance_entered` 因旧 CRLF drop-in 失败，以及共享扫描器、
BOM 防绕过、失败回执保留审计和不手工重写服务器文件的决定。

- [ ] **步骤 2：执行完成前验证**

```powershell
python -m pytest tests/test_systemd_condition_syntax.py tests/test_systemd_condition_guard.py tests/test_reindex_codegraph_maintenance_guard.py tests/test_runtime_deployment_guard.py -q
$files = @(rg --files tests | Where-Object { $_ -match '(^|[\\/])test_.*runtime.*\.py$' })
python -m pytest @files -q
python -m ruff check .
git diff --check
```

预期：测试、Ruff 和差异检查全部通过。

- [ ] **步骤 3：只提交并推送本地 Gitea**

```powershell
git add -- codev_platform/ops/systemd_condition_syntax.py `
  codev_platform/ops/systemd_condition_guard.py `
  codev_platform/ops/reindex_codegraph_maintenance_guard.py `
  tests/test_systemd_condition_syntax.py `
  tests/test_systemd_condition_guard.py `
  tests/test_reindex_codegraph_maintenance_guard.py `
  docs/plans/roadmap-2026-07-11/daily-summary-2026-07-18.md
git commit -m "fix(systemd): 兼容可信CRLF并封堵条件绕过"
```

提交钩子必须只推送 `origin/dev`；随后核对本地与远端 SHA 完全一致。

- [ ] **步骤 4：用新 SHA 重新执行正式部署**

先在目标控制器中只读证明 `codev-reindex.service` 的全部有效 drop-in，再用
`$hash = git rev-parse HEAD` 取得精确提交并运行 `install-wsl-runtime.sh --commit $hash`。
新 SHA 使用新回执；旧 `safety_unproven`
回执不删除、不改写。继续验证数据库迁移、systemd 暂存、四库重建、MCP、CUDA 与入口验收，
全部通过后才清理旧库、旧运行态和 `.worktrees`。
