#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
unset PYTHONPATH PYTHONHOME
export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1

# 正式服务器部署入口。部署阶段顺序只由 Python 生产组合根维护，本脚本只准备可信控制器、制品和计划。

readonly PRODUCTION_REMOTE="fuwuqi"
readonly PRODUCTION_BRANCH="dev"
readonly RUNTIME_ROOT="/var/lib/codev-platform/runtime"
readonly DEFAULT_SERVICE_USER="helloworld"
readonly DEFAULT_SOURCE_REPO="/home/helloworld/work/codev-platform"
readonly DEFAULT_ARTIFACT_ROOT="/srv/codev-artifacts"
readonly DEFAULT_CONFIG_SOURCE="/home/helloworld/.codev-platform/config.json"
readonly DEFAULT_ENVIRONMENT_SOURCE="/etc/codev-platform/platform.source.env"
readonly GIT_FETCH_TIMEOUT_SEC="120"

SERVICE_USER="$DEFAULT_SERVICE_USER"
SOURCE_REPO="$DEFAULT_SOURCE_REPO"
ARTIFACT_ROOT="$DEFAULT_ARTIFACT_ROOT"
CONFIG_SOURCE="$DEFAULT_CONFIG_SOURCE"
ENVIRONMENT_SOURCE="$DEFAULT_ENVIRONMENT_SOURCE"
PROJECT_ID="codev-platform"
TARGET_REQUEST=""
BASELINE_REQUEST=""
BOOTSTRAP_REQUEST=""
RAW_FREEZE_REQUEST=""
APPROVED_REQUEST=""
LOCK_REQUEST=""
WHEELHOUSE_REQUEST=""
PLAN_ROOT_REQUEST=""
REQUIRE_CUDA=1

TARGET_COMMIT=""
BASELINE_REVISION=""
SERVICE_HOME=""
CONTROLLER_PARENT=""
CONTROLLER_ROOT=""
CONTROLLER_STAGE=""
BOOTSTRAP_PYTHON=""
RAW_FREEZE=""
APPROVED_REQUIREMENTS=""
REQUIREMENTS_LOCK=""
WHEELHOUSE=""
PLAN_ROOT=""
PLAN_PATH=""

log() {
    printf '[正式部署] %s\n' "$*"
}

fail() {
    printf '[正式部署][失败] %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
用法：
  sudo bash scripts/install-wsl-runtime.sh [选项]

默认行为：
  固定抓取 fuwuqi/dev，快进 WSL 服务仓，准备 hash lock/wheelhouse，随后执行可续跑正式部署。

选项：
  --commit <40位SHA>          仅接受等于 fuwuqi/dev 最新 tip 的提交
  --baseline <40位SHA>        首次部署回滚基线；默认取当前受管 release，没有则取目标父提交
  --source-repo <目录>        WSL 服务仓，默认 /home/helloworld/work/codev-platform
  --service-user <用户>       服务账号，默认 helloworld
  --artifact-root <目录>      制品根，默认 /srv/codev-artifacts
  --raw-freeze <文件>         精确 freeze；默认取目标提交控制器快照
  --approved-requirements <文件> 批准索引文件；默认取目标提交控制器快照
  --lock <文件>               hash lock，默认 /srv/codev-artifacts/wsl-runtime.lock
  --wheelhouse <目录>         不可变 wheelhouse，默认 /srv/codev-artifacts/wheelhouse
  --plan-root <目录>          持久部署计划目录，默认 /srv/codev-artifacts/deployment-plans
  --bootstrap-python <文件>   root 可信 Python；默认当前受管 release，首次安装回退 /usr/bin/python3
  --config-source <文件>      机器配置源
  --environment-source <文件> systemd 环境配置源
  --project <project_id>      索引项目，默认 codev-platform
  --cpu                       不强制 CUDA；默认必须有可用 GPU
  -h, --help                  显示帮助

手工下载的大 wheel 请先放入 `<wheelhouse同级>/.<wheelhouse名>.incomplete/`；
脚本只补齐缺失 wheel，成功后原子发布最终 wheelhouse。
EOF
}

require_value() {
    local option="$1"
    local value="${2:-}"
    [[ -n "$value" ]] || fail "$option 缺少参数"
}

parse_args() {
    while (($# > 0)); do
        case "$1" in
            --commit)
                require_value "$1" "${2:-}"
                TARGET_REQUEST="$2"
                shift 2
                ;;
            --baseline)
                require_value "$1" "${2:-}"
                BASELINE_REQUEST="$2"
                shift 2
                ;;
            --source-repo)
                require_value "$1" "${2:-}"
                SOURCE_REPO="$2"
                shift 2
                ;;
            --service-user)
                require_value "$1" "${2:-}"
                SERVICE_USER="$2"
                shift 2
                ;;
            --artifact-root)
                require_value "$1" "${2:-}"
                ARTIFACT_ROOT="$2"
                shift 2
                ;;
            --raw-freeze)
                require_value "$1" "${2:-}"
                RAW_FREEZE_REQUEST="$2"
                shift 2
                ;;
            --approved-requirements)
                require_value "$1" "${2:-}"
                APPROVED_REQUEST="$2"
                shift 2
                ;;
            --lock)
                require_value "$1" "${2:-}"
                LOCK_REQUEST="$2"
                shift 2
                ;;
            --wheelhouse)
                require_value "$1" "${2:-}"
                WHEELHOUSE_REQUEST="$2"
                shift 2
                ;;
            --plan-root)
                require_value "$1" "${2:-}"
                PLAN_ROOT_REQUEST="$2"
                shift 2
                ;;
            --bootstrap-python)
                require_value "$1" "${2:-}"
                BOOTSTRAP_REQUEST="$2"
                shift 2
                ;;
            --config-source)
                require_value "$1" "${2:-}"
                CONFIG_SOURCE="$2"
                shift 2
                ;;
            --environment-source)
                require_value "$1" "${2:-}"
                ENVIRONMENT_SOURCE="$2"
                shift 2
                ;;
            --project)
                require_value "$1" "${2:-}"
                PROJECT_ID="$2"
                shift 2
                ;;
            --cpu)
                REQUIRE_CUDA=0
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                fail "未知参数：$1"
                ;;
        esac
    done
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "缺少命令：$1"
}

run_git_bounded() {
    local timeout_sec="$1"
    shift
    [[ "$timeout_sec" =~ ^[0-9]+$ ]] || fail "Git 超时参数无效"
    local -a command=(git -C "$SOURCE_REPO" "$@")
    if ((timeout_sec > 0)); then
        command=(
            timeout --foreground --signal=TERM --kill-after=5 "$timeout_sec"
            "${command[@]}"
        )
    fi
    runuser -u "$SERVICE_USER" -- env -i \
        "HOME=$SERVICE_HOME" \
        "PATH=/usr/bin:/bin" \
        "GIT_TERMINAL_PROMPT=0" \
        "GCM_INTERACTIVE=Never" \
        "GIT_ASKPASS=/bin/false" \
        "${command[@]}"
}

run_git() {
    run_git_bounded 0 "$@"
}

prepare_source_repository() {
    [[ "$SERVICE_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || fail "服务账号格式无效"
    SERVICE_HOME="$(getent passwd "$SERVICE_USER" | awk -F: 'NR == 1 {print $6}')"
    [[ -n "$SERVICE_HOME" ]] || fail "服务账号不存在"
    [[ -d "$SOURCE_REPO" && ! -L "$SOURCE_REPO" ]] || fail "WSL 服务仓不可用"
    [[ "$(stat -c '%U' "$SOURCE_REPO")" == "$SERVICE_USER" ]] || fail "WSL 服务仓所有者错误"
    [[ "$(run_git symbolic-ref --quiet --short HEAD)" == "$PRODUCTION_BRANCH" ]] \
        || fail "WSL 服务仓必须位于 dev 分支"
    [[ -z "$(run_git status --porcelain=v1 --untracked-files=no)" ]] \
        || fail "WSL 服务仓存在 tracked 改动，拒绝覆盖"

    local remote_url lowered tracking_ref remote_tip current
    remote_url="$(run_git remote get-url "$PRODUCTION_REMOTE")" \
        || fail "fuwuqi 远端不可读"
    lowered="${remote_url,,}"
    [[ -n "$remote_url" && "$lowered" != *"github.com"* ]] \
        || fail "fuwuqi 远端禁止指向 GitHub"
    tracking_ref="refs/remotes/${PRODUCTION_REMOTE}/${PRODUCTION_BRANCH}"
    log "获取固定远端 fuwuqi/dev"
    run_git_bounded "$GIT_FETCH_TIMEOUT_SEC" fetch --quiet --no-tags "$PRODUCTION_REMOTE" \
        "+refs/heads/${PRODUCTION_BRANCH}:${tracking_ref}" \
        || fail "获取 fuwuqi/dev 失败"
    remote_tip="$(run_git rev-parse --verify "${tracking_ref}^{commit}")"
    [[ "$remote_tip" =~ ^[0-9a-f]{40}$ ]] || fail "fuwuqi/dev tip 无效"

    if [[ -n "$TARGET_REQUEST" ]]; then
        [[ "$TARGET_REQUEST" =~ ^[0-9a-f]{40}$ ]] || fail "目标提交必须是完整小写 SHA"
        [[ "$TARGET_REQUEST" == "$remote_tip" ]] || fail "目标提交不是 fuwuqi/dev 最新 tip"
        TARGET_COMMIT="$TARGET_REQUEST"
    else
        TARGET_COMMIT="$remote_tip"
    fi

    current="$(run_git rev-parse --verify 'HEAD^{commit}')"
    if [[ "$current" != "$TARGET_COMMIT" ]]; then
        log "快进 WSL 服务仓到目标提交"
        run_git merge --ff-only "$tracking_ref" >/dev/null \
            || fail "WSL 服务仓不能安全快进到 fuwuqi/dev"
    fi
    [[ "$(run_git rev-parse --verify 'HEAD^{commit}')" == "$TARGET_COMMIT" ]] \
        || fail "WSL 服务仓没有到达目标提交"
    [[ -z "$(run_git status --porcelain=v1 --untracked-files=no)" ]] \
        || fail "WSL 服务仓快进后发生漂移"
}

cleanup_controller_stage() {
    if [[ -n "$CONTROLLER_STAGE" && -d "$CONTROLLER_STAGE" ]]; then
        case "$CONTROLLER_STAGE" in
            "$CONTROLLER_PARENT"/."$TARGET_COMMIT".incomplete.*)
                chmod -R u+w "$CONTROLLER_STAGE" 2>/dev/null || true
                rm -rf -- "$CONTROLLER_STAGE"
                ;;
        esac
    fi
}

verify_controller_snapshot() {
    [[ -d "$CONTROLLER_ROOT" && ! -L "$CONTROLLER_ROOT" ]] \
        || fail "root 控制器快照不可用"
    [[ "$(<"$CONTROLLER_ROOT/.controller-revision")" == "$TARGET_COMMIT" ]] \
        || fail "root 控制器提交身份不一致"
    local unexpected unsafe
    unexpected="$(find "$CONTROLLER_ROOT" -mindepth 1 ! -type d ! -type f -print -quit)"
    [[ -z "$unexpected" ]] || fail "root 控制器含链接或特殊文件"
    unsafe="$(find "$CONTROLLER_ROOT" -mindepth 0 \( ! -user root -o -perm /022 \) -print -quit)"
    [[ -z "$unsafe" ]] || fail "root 控制器所有权或权限不安全"
}

prepare_controller_snapshot() {
    CONTROLLER_PARENT="$RUNTIME_ROOT/deployers"
    CONTROLLER_ROOT="$CONTROLLER_PARENT/$TARGET_COMMIT"
    install -d -m 0755 -o root -g root "$RUNTIME_ROOT" "$CONTROLLER_PARENT"
    if [[ -e "$CONTROLLER_ROOT" || -L "$CONTROLLER_ROOT" ]]; then
        verify_controller_snapshot
        return
    fi

    CONTROLLER_STAGE="$CONTROLLER_PARENT/.${TARGET_COMMIT}.incomplete.$$"
    install -d -m 0700 -o root -g root "$CONTROLLER_STAGE"
    log "从目标提交构造 root 只读控制器快照"
    run_git archive --format=tar "$TARGET_COMMIT" -- \
        codev_platform requirements/wsl-runtime.freeze requirements-runtime.txt \
        | tar --extract --file=- --directory="$CONTROLLER_STAGE" \
            --no-same-owner --no-same-permissions
    local unexpected
    unexpected="$(find "$CONTROLLER_STAGE" -mindepth 1 ! -type d ! -type f -print -quit)"
    [[ -z "$unexpected" ]] || fail "目标提交控制器含链接或特殊文件"
    printf '%s\n' "$TARGET_COMMIT" >"$CONTROLLER_STAGE/.controller-revision"
    chown -R root:root "$CONTROLLER_STAGE"
    chmod -R u=rwX,go=rX "$CONTROLLER_STAGE"
    mv -- "$CONTROLLER_STAGE" "$CONTROLLER_ROOT"
    CONTROLLER_STAGE=""
    verify_controller_snapshot
}

require_root_trusted_path() {
    local requested="$1"
    [[ "$requested" == /* && "$requested" != /mnt/* && "$requested" != /home/* ]] \
        || fail "bootstrap Python 必须位于 root 可信路径"
    local resolved current owner mode numeric
    resolved="$(realpath -e "$requested")" || fail "bootstrap Python 不存在"
    [[ -f "$resolved" && -x "$requested" ]] || fail "bootstrap Python 不可执行"
    current="$resolved"
    while :; do
        owner="$(stat -Lc '%U' "$current")"
        mode="$(stat -Lc '%a' "$current")"
        numeric=$((8#$mode))
        [[ "$owner" == "root" && $((numeric & 8#022)) -eq 0 ]] \
            || fail "bootstrap Python 路径可被非 root 修改"
        [[ "$current" == "/" ]] && break
        current="$(dirname "$current")"
    done
}

resolve_bootstrap_python() {
    if [[ -n "$BOOTSTRAP_REQUEST" ]]; then
        BOOTSTRAP_PYTHON="$BOOTSTRAP_REQUEST"
    elif [[ -x "$RUNTIME_ROOT/current/venv/bin/python" ]]; then
        BOOTSTRAP_PYTHON="$RUNTIME_ROOT/current/venv/bin/python"
    else
        BOOTSTRAP_PYTHON="/usr/bin/python3"
    fi
    require_root_trusted_path "$BOOTSTRAP_PYTHON"
}

run_controller_cli() {
    "$BOOTSTRAP_PYTHON" -I -B -c '
import runpy
import sys

controller = sys.argv.pop(1)
sys.path.insert(0, controller)
runpy.run_module("codev_platform.cli", run_name="__main__", alter_sys=True)
' "$CONTROLLER_ROOT" "$@"
}

prepare_artifacts() {
    RAW_FREEZE="${RAW_FREEZE_REQUEST:-$CONTROLLER_ROOT/requirements/wsl-runtime.freeze}"
    APPROVED_REQUIREMENTS="${APPROVED_REQUEST:-$CONTROLLER_ROOT/requirements-runtime.txt}"
    REQUIREMENTS_LOCK="${LOCK_REQUEST:-$ARTIFACT_ROOT/wsl-runtime.lock}"
    WHEELHOUSE="${WHEELHOUSE_REQUEST:-$ARTIFACT_ROOT/wheelhouse}"
    [[ -f "$RAW_FREEZE" && ! -L "$RAW_FREEZE" ]] || fail "精确 freeze 不可用"
    [[ -f "$APPROVED_REQUIREMENTS" && ! -L "$APPROVED_REQUIREMENTS" ]] \
        || fail "批准索引文件不可用"
    install -d -m 0755 -o root -g root "$(dirname "$REQUIREMENTS_LOCK")" "$(dirname "$WHEELHOUSE")"
    if [[ ! -f "$REQUIREMENTS_LOCK" || ! -d "$WHEELHOUSE" ]]; then
        log "生成或续跑不可变 wheelhouse 与 hash lock"
        run_controller_cli runtime lock \
            --raw-freeze "$RAW_FREEZE" \
            --approved-requirements "$APPROVED_REQUIREMENTS" \
            --output "$REQUIREMENTS_LOCK" \
            --wheelhouse "$WHEELHOUSE"
    else
        log "复用既有 hash lock 与不可变 wheelhouse，部署阶段将完整复验"
    fi
}

current_runtime_revision() {
    "$BOOTSTRAP_PYTHON" -I -B -c '
import sys
from pathlib import Path

controller = sys.argv.pop(1)
sys.path.insert(0, controller)
try:
    from codev_platform.runtime_release_binding import snapshot_current_release
    print(snapshot_current_release(Path(sys.argv[1])).runtime_revision)
except Exception:
    raise SystemExit(1)
' "$CONTROLLER_ROOT" "$RUNTIME_ROOT"
}

resolve_baseline_revision() {
    if [[ -n "$BASELINE_REQUEST" ]]; then
        [[ "$BASELINE_REQUEST" =~ ^[0-9a-f]{40}$ ]] || fail "基线提交必须是完整小写 SHA"
        BASELINE_REVISION="$BASELINE_REQUEST"
    else
        BASELINE_REVISION="$(current_runtime_revision 2>/dev/null || true)"
        if [[ -z "$BASELINE_REVISION" ]]; then
            BASELINE_REVISION="$(run_git rev-parse --verify "${TARGET_COMMIT}^")"
        fi
    fi
    [[ "$BASELINE_REVISION" =~ ^[0-9a-f]{40}$ ]] || fail "无法确定独立回滚基线"
    [[ "$BASELINE_REVISION" != "$TARGET_COMMIT" ]] \
        || fail "当前运行时已是目标提交但缺少持久计划，拒绝伪造基线"
    run_git merge-base --is-ancestor "$BASELINE_REVISION" \
        "refs/remotes/${PRODUCTION_REMOTE}/${PRODUCTION_BRANCH}" \
        || fail "回滚基线不属于 fuwuqi/dev 受信历史"
}

prepare_deployment_plan() {
    PLAN_ROOT="${PLAN_ROOT_REQUEST:-$ARTIFACT_ROOT/deployment-plans}"
    install -d -m 0700 -o root -g root "$PLAN_ROOT"
    PLAN_PATH="$PLAN_ROOT/$TARGET_COMMIT.json"
    if [[ ! -e "$PLAN_PATH" && ! -L "$PLAN_PATH" ]]; then
        resolve_baseline_revision
    fi
    "$BOOTSTRAP_PYTHON" -I -B - \
        "$CONTROLLER_ROOT" "$PLAN_PATH" "$TARGET_COMMIT" "$BASELINE_REVISION" \
        "$SOURCE_REPO" "$RUNTIME_ROOT" "$REQUIREMENTS_LOCK" \
        "$APPROVED_REQUIREMENTS" "$WHEELHOUSE" \
        "/var/tmp/codev-platform-candidates/$SERVICE_USER" \
        "$CONFIG_SOURCE" "$ENVIRONMENT_SOURCE" "$SERVICE_USER" "$PROJECT_ID" \
        "$REQUIRE_CUDA" <<'PY'
import json
import os
from pathlib import Path
import stat
import sys

controller = sys.argv[1]
sys.path.insert(0, controller)
from codev_platform.ops.runtime_deploy import load_deployment_plan
from codev_platform.runtime_deployment_contract import DeploymentPlan

(
    _controller,
    plan_text,
    target,
    baseline,
    source_repo,
    runtime_root,
    requirements_lock,
    approved_requirements,
    wheelhouse,
    candidate_root,
    config_source,
    environment_source,
    service_user,
    project_id,
    require_cuda,
) = sys.argv[1:]
path = Path(plan_text)
values = {
    "schema_version": 1,
    "target_revision": target,
    "baseline_revision": baseline or None,
    "source_repo": source_repo,
    "runtime_root": runtime_root,
    "requirements_lock": requirements_lock,
    "approved_requirements": approved_requirements,
    "wheelhouse": wheelhouse,
    "candidate_root": candidate_root,
    "config_source": config_source,
    "environment_source": environment_source,
    "service_user": service_user,
    "project_id": project_id,
    "require_cuda": require_cuda == "1",
}
if path.exists() or path.is_symlink():
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise SystemExit("既有部署计划元数据不受信任")
    existing = load_deployment_plan(path)
    values["baseline_revision"] = existing.baseline_revision
    expected = DeploymentPlan(**values)
    if existing != expected:
        raise SystemExit("既有部署计划与本次固定输入不一致")
    raise SystemExit(0)

plan = DeploymentPlan(**values)
encoded = (
    json.dumps(
        plan.to_mapping(),
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    + "\n"
).encode("utf-8")
temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
descriptor = None
try:
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    offset = 0
    while offset < len(encoded):
        written = os.write(descriptor, encoded[offset:])
        if written <= 0:
            raise OSError("计划写入未取得进展")
        offset += written
    os.fsync(descriptor)
    os.close(descriptor)
    descriptor = None
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    if descriptor is not None:
        os.close(descriptor)
    temporary.unlink(missing_ok=True)
PY
}

main() {
    parse_args "$@"
    [[ "$(id -u)" == "0" ]] || fail "必须以 root 运行"
    for command in git runuser env getent awk stat find tar install realpath flock; do
        require_command "$command"
    done
    require_command timeout
    require_command setpriv
    exec 9>/run/lock/codev-platform-runtime-deploy.lock
    flock -n 9 || fail "已有正式部署任务正在执行"

    prepare_source_repository
    prepare_controller_snapshot
    resolve_bootstrap_python
    prepare_artifacts
    prepare_deployment_plan

    log "执行目标 $TARGET_COMMIT 的可续跑生产部署"
    run_controller_cli runtime deploy --plan "$PLAN_PATH"
    log "正式部署与最终验收完成"
}

trap cleanup_controller_stage EXIT
trap 'printf "[正式部署][失败] 第 %s 行执行失败\n" "$LINENO" >&2' ERR
main "$@"
