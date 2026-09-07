# WSL 内容寻址运行时首次切换与回滚手册

## 1. 适用范围与安全边界

本手册只用于 Linux/WSL 上已经完成任务 3～9 的内容寻址运行时。默认只执行只读预检；
未设置显式授权变量时，不构建基座、不暂存版本、不切换 `current/previous`，也不操作 systemd。

- 本手册不下载或升级 Torch、CUDA、Chroma；禁止生成正式 requirements lock，只消费已审核文件。
- 禁止手工删除 current、previous、`.incomplete`、quarantine 或失败版本。
- 不修改数据库 schema，不删除索引库，不把 detached release 当作 reindex 输入仓。
- 索引输入始终是配置中的稳定 live clone，并用完整目标提交 SHA 物化。
- 凭据只从既有 root 环境文件或进程环境读取，不打印 token、DSN 或完整配置。
- 新旧版本可共享同一 `base_id`，但必须各自拥有独立修订、wheel 和 `release_id`。

若与[隔离 Reindex 维护窗口手册](./reindex-isolated-maintenance-wsl-runbook-2026-07-13.md)冲突，
以后者对维护 marker、CodeGraph hold/runtime mask 和恢复状态机的要求为准。

## 2. 固定变量

路径按本机填写。`BUILDER_PYTHON` 必须已安装当前发布工具并能以 `-I` 导入平台；本手册不安装它。

```bash
set -euo pipefail
export REPO=/home/user/project RUNTIME_ROOT=/var/lib/codev-platform/runtime
export BUILDER_PYTHON=/home/user/project SERVICE_USER=helloworld PROJECT_ID=codev-platform
export RUNTIME_LOCK="$REPO/requirements/wsl-runtime.lock" APPROVED_REQUIREMENTS="$REPO/requirements-runtime.txt"
export CANDIDATE_ROOT=/var/tmp/codev-runtime-candidates CODEV_PLATFORM_CONFIG=/home/user/project
runtime_user() { env CODEV_PLATFORM_CONFIG="$CODEV_PLATFORM_CONFIG" "$BUILDER_PYTHON" -I -m codev_platform.cli runtime --runtime-root "$RUNTIME_ROOT" "$@"; }
runtime_root() { sudo -n env CODEV_PLATFORM_CONFIG="$CODEV_PLATFORM_CONFIG" "$BUILDER_PYTHON" -I -m codev_platform.cli runtime --runtime-root "$RUNTIME_ROOT" "$@"; }
assert_dead() {
  local unit group
  for unit in "$@"; do
    test "$(systemctl show --property=MainPID --value "$unit")" = 0
    group="$(systemctl show --property=ControlGroup --value "$unit")"
    test -z "$group" || { test "$group" != /; test ! -e "/sys/fs/cgroup$group/cgroup.procs" || test ! -s "/sys/fs/cgroup$group/cgroup.procs"; }
  done
}
```

不要把 `CODEV_PLATFORM_MCP_TOKEN` 写入命令历史；真实 MCP 冒烟前从既有受控环境加载。

## 3. 只读预检

```bash
test -x "$BUILDER_PYTHON"
test -f "$CODEV_PLATFORM_CONFIG" && test -f "$RUNTIME_LOCK" && test -f "$APPROVED_REQUIREMENTS"
git -C "$REPO" diff --quiet && git -C "$REPO" diff --cached --quiet
git -C "$REPO" ls-files --error-unmatch requirements/wsl-runtime.lock && \
  git -C "$REPO" ls-files --error-unmatch requirements-runtime.txt
"$BUILDER_PYTHON" -I -m codev_platform.runtime_lock validate \
  "$RUNTIME_LOCK" --approved "$APPROVED_REQUIREMENTS"
test ! -e "$RUNTIME_ROOT/current" || runtime_user status --json
sudo -n true && systemctl is-system-running --wait
systemctl list-units 'codev-*' --all --no-pager
```

读取并验证三个 Plan C 锚点；文件缺失、不是完整提交或顺序错误时停止。

```bash
LEAF_BASE_PATH="$(git -C "$REPO" rev-parse --git-path plan-c-leaf-base)" LEAF_HEAD_PATH="$(git -C "$REPO" rev-parse --git-path plan-c-leaf-head)"
MAIN_BASE_PATH="$(git -C "$REPO" rev-parse --git-path plan-c-main-base)"
LEAF_BASE="$(tr -d '\r\n' < "$LEAF_BASE_PATH")"
LEAF_HEAD="$(tr -d '\r\n' < "$LEAF_HEAD_PATH")"
MAIN_BASE="$(tr -d '\r\n' < "$MAIN_BASE_PATH")"
for oid in "$LEAF_BASE" "$LEAF_HEAD" "$MAIN_BASE"; do git -C "$REPO" cat-file -e "$oid^{commit}"; done
export TARGET_SHA="$(git -C "$REPO" rev-parse HEAD)"; printf '%s\n' "$TARGET_SHA" | grep -Eq '^[0-9a-f]{40}([0-9a-f]{24})?$'
```

若现有 `current` 存在，只读执行 `runtime_user status --json` 和受保护状态接口，记录完整
`release_id/base_id/runtime_revision`；公共 `/health`、`/healthz` 不得出现这些字段。

## 4. 显式变更授权

只有已批准维护窗口的操作者才能继续。每个后续变更代码块都应保留同一门禁。

```bash
test "${CODEV_RUNTIME_APPLY:-否}" = "是" || {
  echo '未设置 CODEV_RUNTIME_APPLY=是；保持只读并退出' >&2
  exit 2
}
```

## 5. 构建并验证基线版本

先构建或复用共享基座，再从 `plan-c-main-base` 创建仓外、干净、detached worktree。禁止从
当前 HEAD、脏目录或手工复制文件伪造基线。

```bash
test "${CODEV_RUNTIME_APPLY:-否}" = "是"
mkdir -p "$CANDIDATE_ROOT"
BASE_JSON="$(runtime_root base --lock "$RUNTIME_LOCK" \
  --approved-requirements "$APPROVED_REQUIREMENTS")"
BASE_ID="$(jq -er '.result.base_id | select(test("^[0-9a-f]{64}$"))' <<<"$BASE_JSON")"
export BASE_ID
BASELINE_PARENT="$(mktemp -d /var/tmp/codev-main-base.XXXXXX)"
BASELINE_WORKTREE="$BASELINE_PARENT/source"
git -C "$REPO" worktree add --detach "$BASELINE_WORKTREE" "$MAIN_BASE"
cleanup_baseline() {
  git -C "$REPO" worktree remove --force "$BASELINE_WORKTREE" 2>/dev/null || true
  rmdir "$BASELINE_PARENT" 2>/dev/null || true
}
trap cleanup_baseline EXIT
BASELINE_BUILD="$(runtime_user build --repo "$BASELINE_WORKTREE" --out-dir "$CANDIDATE_ROOT")"
BASELINE_REVISION="$(jq -er '.result.runtime_revision' <<<"$BASELINE_BUILD")"
BASELINE_WHEEL_SHA="$(jq -er '.result.wheel_sha256' <<<"$BASELINE_BUILD")"
BASELINE_WHEEL_NAME="$(jq -er '.result.wheel_name' <<<"$BASELINE_BUILD")"
test "$BASELINE_REVISION" = "$MAIN_BASE"
BASELINE_CANDIDATE_ID="$(printf '%s\n%s\n' "$BASELINE_REVISION" "$BASELINE_WHEEL_SHA" | sha256sum | awk '{print $1}')"
BASELINE_CANDIDATE="$CANDIDATE_ROOT/$BASELINE_CANDIDATE_ID"
BASELINE_STAGE="$(runtime_root stage \
  --candidate "$BASELINE_CANDIDATE/$BASELINE_WHEEL_NAME.candidate.json" \
  --wheel "$BASELINE_CANDIDATE/$BASELINE_WHEEL_NAME" --base-id "$BASE_ID")"
BASELINE_RELEASE_ID="$(jq -er '.result.release_id | select(test("^[0-9a-f]{64}$"))' <<<"$BASELINE_STAGE")"
runtime_user verify "$BASELINE_RELEASE_ID"
cleanup_baseline; trap - EXIT
export BASELINE_RELEASE_ID
```

验证基线版本解释器能以 `-I` 导入 `codev_platform.ops.memory_maintenance`。失败时不得首次激活。

## 6. 构建并验证新版本

```bash
test "${CODEV_RUNTIME_APPLY:-否}" = "是"
test -z "$(git -C "$REPO" status --porcelain --untracked-files=normal)"
NEW_BUILD="$(runtime_user build --repo "$REPO" --out-dir "$CANDIDATE_ROOT")"
NEW_REVISION="$(jq -er '.result.runtime_revision' <<<"$NEW_BUILD")"
NEW_WHEEL_SHA="$(jq -er '.result.wheel_sha256' <<<"$NEW_BUILD")"
NEW_WHEEL_NAME="$(jq -er '.result.wheel_name' <<<"$NEW_BUILD")"
test "$NEW_REVISION" = "$TARGET_SHA"
NEW_CANDIDATE_ID="$(printf '%s\n%s\n' "$NEW_REVISION" "$NEW_WHEEL_SHA" | sha256sum | awk '{print $1}')"
NEW_CANDIDATE="$CANDIDATE_ROOT/$NEW_CANDIDATE_ID"
NEW_STAGE="$(runtime_root stage \
  --candidate "$NEW_CANDIDATE/$NEW_WHEEL_NAME.candidate.json" \
  --wheel "$NEW_CANDIDATE/$NEW_WHEEL_NAME" --base-id "$BASE_ID")"
NEW_RELEASE_ID="$(jq -er '.result.release_id | select(test("^[0-9a-f]{64}$"))' <<<"$NEW_STAGE")"
test "$NEW_RELEASE_ID" != "$BASELINE_RELEASE_ID"
runtime_user verify "$NEW_RELEASE_ID"
export NEW_RELEASE_ID
```

## 7. 形成可回滚双版本状态

旧服务继续运行时先激活基线再激活新版本；旧进程仍须报告旧身份，指针切换不等于重启。

```bash
test "${CODEV_RUNTIME_APPLY:-否}" = "是"
runtime_root activate "$BASELINE_RELEASE_ID"
runtime_user verify "$BASELINE_RELEASE_ID"
runtime_root activate "$NEW_RELEASE_ID"
runtime_user verify "$NEW_RELEASE_ID"
CURRENT_RELEASE_ID="$(basename "$(readlink -e "$RUNTIME_ROOT/current")")"
PREVIOUS_RELEASE_ID="$(basename "$(readlink -e "$RUNTIME_ROOT/previous")")"
test "$CURRENT_RELEASE_ID" = "$NEW_RELEASE_ID"
test -n "$PREVIOUS_RELEASE_ID"
test "$PREVIOUS_RELEASE_ID" = "$BASELINE_RELEASE_ID"
runtime_user verify "$PREVIOUS_RELEASE_ID"
sudo -n -u "$SERVICE_USER" "$RUNTIME_ROOT/previous/venv/bin/python" -I -c \
  'import codev_platform.ops.memory_maintenance'
export CURRENT_RELEASE_ID PREVIOUS_RELEASE_ID
```

若 `previous` 为空、不是基线、基座不可验证或 maintenance 导入失败，禁止进入生产维护窗口。

## 8. 预装与目标用户探针

以服务用户生成 `--no-restart` 材料，审核后才执行 root 脚本。脚本须先运行目标用户探针；只可
复制 unit、reload 和 enable，不得执行受管服务的 start/stop/restart/reset-failed。

```bash
test "${CODEV_RUNTIME_APPLY:-否}" = "是"
sudo -n -u "$SERVICE_USER" env CODEV_PLATFORM_CONFIG="$CODEV_PLATFORM_CONFIG" \
  "$RUNTIME_ROOT/current/venv/bin/python" -I -m codev_platform.cli \
  serve-mcp install-systemd --runtime-root "$RUNTIME_ROOT" --no-restart
sudo -n "$(getent passwd "$SERVICE_USER" | cut -d: -f6)/codev-systemd/install.sh"
```

执行前后记录全部受管 unit 的 MainPID/InvocationID；任何运行态变化都视为预装失败。

## 9. 维护窗口切换

先关入口、等待 active attempt 清零，再由维护状态机停 reindex/CodeGraph 并证明 cgroup 无残留。

```bash
test "${CODEV_RUNTIME_APPLY:-否}" = "是"
sudo -n systemctl stop codev-webhook.service
sudo -n -u "$SERVICE_USER" "$RUNTIME_ROOT/current/venv/bin/python" -I -m \
  codev_platform.cli reindex-queue status
# 人工确认 active attempt 清零后继续；pending 可保留，不得 break-lease 或 prune-stale。
sudo -n "$RUNTIME_ROOT/current/venv/bin/python" -I -m codev_platform.cli \
  reindex-maintenance prepare --yes
assert_dead codev-reindex.service codev-mcp-codegraph.service
sudo -n systemctl stop codev-mcp-platform-docs.service codev-mcp-agent-memory.service \
  codev-mcp-graph.service codev-agent.service codev-web.service
assert_dead codev-mcp-platform-docs.service codev-mcp-agent-memory.service \
  codev-mcp-graph.service codev-agent.service codev-web.service
sudo -n systemctl restart codev-mcp-platform-docs.service codev-mcp-agent-memory.service \
  codev-mcp-graph.service codev-agent.service codev-web.service
sudo -n -u "$SERVICE_USER" "$RUNTIME_ROOT/current/venv/bin/python" -I -m codev_platform.cli runtime status --json --runtime-root "$RUNTIME_ROOT"
```

逐个证明 MainPID 的 `/proc/<pid>/exe`、有效 ExecStart、受保护状态身份均指向新 release；公共健康面
仍不得泄露运行身份。webhook、reindex 和 CodeGraph 此时仍保持受控停止/待命边界。

## 10. 受控 reindex、真实冒烟与恢复入口

### 10.1 恢复 reindex 待命态

严格执行隔离维护手册第 5 节的 `restore --yes`；补偿证据不完整时保持停机，不得手工删门禁。

### 10.2 受控 reindex

严格执行 `reindex-isolated-maintenance-wsl-runbook-2026-07-13.md` 第 6 节：用 live clone 的目标提交完整 SHA
入队，等待 `chroma`、`codegraph`、`ingest`、`code_vec` 四项均为 `ok` 且目标 SHA 完全一致。
此前必须已经证明 active attempt 清零。不得把队列为空当作成功，也不得以旧 manifest 通过。

仅在 `codegraph` manifest 成功后执行受控 `resume-codegraph --yes`；首次 MCP 查询可能触发追赶同步，
因此真实 CodeGraph 调用必须放在恢复证明之后。

### 10.3 真实 MCP 冒烟

仅 HTTP 200 不算成功；脚本要求真实 SSE 结果可解码为无错误的结构化对象。

```python
import asyncio, json, os
from urllib.parse import quote
from mcp import ClientSession
from mcp.client.sse import sse_client
async def call(url, tool, arguments):
    token = os.environ["CODEV_PLATFORM_MCP_TOKEN"]
    separator = "&" if "?" in url else "?"
    url = f'{url}{separator}project_id={quote(os.environ["PROJECT_ID"], safe="")}'
    headers = {"Authorization": f"Bearer {token}"}
    async with sse_client(url, headers=headers) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments)
    if result.isError or not result.content: raise RuntimeError(f"{tool} 调用失败")
    texts = [item.text for item in result.content if getattr(item, "type", None) == "text"]
    if not texts: raise RuntimeError(f"{tool} 缺少结构化文本")
    payload = json.loads(texts[0])
    if not isinstance(payload, dict) or payload.get("error") or payload.get("ok") is False or payload.get("failures"): raise RuntimeError(f"{tool} 返回错误对象")
    return payload
async def main():
    await call(os.environ["PLATFORM_DOCS_URL"], "list_collections", {})
    await call(os.environ["CODEGRAPH_URL"], "codegraph_status", {"_codev_merge": "structured"})
    await call(os.environ["GRAPH_URL"], "search_nodes", {"query": "__runtime_smoke__", "limit": 1})
    await call(os.environ["AGENT_MEMORY_URL"], "recall", {"query": "__runtime_smoke__", "limit": 1})
    print(json.dumps({"ok": True, "project_id": os.environ["PROJECT_ID"]}, ensure_ascii=False))
asyncio.run(main())
```

URL 使用配置解析出的四个 `/sse` 地址并带 `project_id`；不得把 token 写进 URL、文档或验收记录。

### 10.4 恢复 webhook

四项 manifest、新身份、真实 MCP 和稳定窗口通过后最后启动 webhook，并执行一次受签名普通 push 验收。

## 11. 回滚

回滚不做 schema downgrade。先停入口和新服务、证明进程树死亡，再验证 `previous` 及其基座。

```bash
test "${CODEV_RUNTIME_APPLY:-否}" = "是"
sudo -n systemctl stop codev-webhook.service
sudo -n "$RUNTIME_ROOT/current/venv/bin/python" -I -m codev_platform.cli reindex-maintenance prepare --yes
sudo -n systemctl stop codev-mcp-platform-docs.service codev-mcp-codegraph.service \
  codev-mcp-agent-memory.service codev-mcp-graph.service codev-agent.service codev-web.service
assert_dead codev-reindex.service codev-mcp-platform-docs.service codev-mcp-codegraph.service \
  codev-mcp-agent-memory.service codev-mcp-graph.service codev-agent.service codev-web.service
PREVIOUS_RELEASE_ID="$(basename "$(readlink -e "$RUNTIME_ROOT/previous")")"
runtime_user verify "$PREVIOUS_RELEASE_ID"
runtime_root rollback
sudo -n systemctl restart codev-mcp-platform-docs.service codev-mcp-agent-memory.service \
  codev-mcp-graph.service codev-agent.service codev-web.service
ROLLBACK_IDENTITY_JSON="$(sudo -n -u "$SERVICE_USER" "$RUNTIME_ROOT/current/venv/bin/python" -I -c 'import json; from codev_platform.core.runtime_identity import runtime_identity; print(json.dumps(runtime_identity().as_dict(), sort_keys=True))')"
test "$(jq -er '.release_id' <<<"$ROLLBACK_IDENTITY_JSON")" = "$PREVIOUS_RELEASE_ID"
```

随后按基线版本可用的维护入口重复第 10.2 节受控 reindex，再执行第 10.3 节真实 MCP 冒烟；最后
恢复 webhook。禁止 schema downgrade；不得删除失败版本、`.incomplete`、quarantine 和审计证据。

## 12. 收尾与证据

记录但不输出凭据：baseline/new/previous 的完整 `release_id`、共享 `base_id`、完整 Git SHA、wheel
摘要、四项 manifest、systemd MainPID/InvocationID、真实 MCP 结构化结果和回滚演练时间。

检查两段 Plan C 范围没有修改 reindex 实现，并检查补丁格式：

```bash
git -C "$REPO" diff --name-only "$LEAF_BASE..$LEAF_HEAD" | grep '^codev_platform/reindex/' && exit 1 || true
git -C "$REPO" diff --name-only "$MAIN_BASE..HEAD" | grep '^codev_platform/reindex/' && exit 1 || true
git -C "$REPO" diff --check "$LEAF_BASE..$LEAF_HEAD"
git -C "$REPO" diff --check "$MAIN_BASE..HEAD"
```

失败版本与 .incomplete 证据必须保留取证。只有版本不再被 `current/previous`、systemd、进程、任务或
审计引用，并完成单独清理审批后，才允许精确清理；本手册不执行该清理。
