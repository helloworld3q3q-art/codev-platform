r"""codev_platform.ops.health -- cross-platform tool-stack health check.

Port of scripts/ai-health.ps1 to a config-driven, cross-platform Python CLI
subcommand (`codev-platform health`). All machine-specific paths come from
~/.codev-platform/config.json via codev_platform.ops._common / core (no hardcoded
D:\models etc). Windows-only PowerShell plumbing (CIM process probes, nvidia-smi
parsing) is reimplemented portably or degraded to INFO on non-supported platforms.

2026-06-02 拆包 (file-discipline §1: 单文件 ≤600 行): 原单文件 health.py 拆为
  - _util.py   Report + 文件/进程/git/jsonl helper
  - _checks.py 各项 _check_* 体检
  - _usage.py  近 7 天使用率统计
  - __init__.py (本文件) cmd_health / cmd_health_all / register + re-export 全部原名
对外行为零变更: `from codev_platform.ops import health; health.register(sub)` 与
`health.Report` / `health._check_*` 等引用全部沿用 (本文件顶部 re-export)。

Checks (parity with the .ps1):
  - chroma venv python present
  - embed model dir + load probe (Full only)
  - reranker model dir
  - torch + CUDA probe (Full only)
  - chroma data dir + collection probe (Full) / .last_build stamp (Light)
  - chroma index freshness vs latest docs/rules mtime
  - platform-docs daemon /health (HTTP)
  - platform-docs server process count
  - mcp-proxy presence
  - rules vs incident freshness
  - codegraph db (integrity + journal_mode + counts) + locks
  - post-commit hook missed-fire detection
  - git tools/ status
  - usage stats: search_recall / reindex 7d / platform-docs usage+adopt /
    codegraph usage

ExitCode: 0 all green / 2 only WARN / 1 any FAIL (same as .ps1).
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from codev_platform.ops._common import (
    cfg_get,
    chroma_python,
    codev_root,
    config as load_cfg,
    meta_health,
    out,
    project_id_of,
    resolve_repo,
)
from codev_platform.core.paths import chroma_dir

# re-export 全部原 module 级符号 (向后兼容: 外部/测试 `health.<name>` 沿用)
from ._util import (  # noqa: F401
    Report,
    _count_files,
    _expand,
    _git,
    _iter_jsonl,
    _latest_mtime,
    _list_processes,
    _parse_dt,
    _run_py,
)
from ._checks import (  # noqa: F401
    _check_chroma_data,
    _check_chroma_freshness,
    _check_chroma_venv,
    _check_codegraph_db,
    _check_codegraph_mcp,
    _check_daemon,
    _check_embed_load,
    _check_embed_model,
    _check_git_tools,
    _check_graph_store,
    _check_hook_missed,
    _check_mcp_proxy,
    _check_pd_servers,
    _check_reindex_worker,
    _check_reranker,
    _check_rules_vs_incident,
    _check_torch,
    _check_webhook_extra_repo_mapping,
    _daemon_port,
    _resolve_model_dir,
)
from ._usage import (  # noqa: F401
    _usage_codegraph,
    _usage_platform_docs,
    _usage_reindex,
    _usage_search_recall,
)


# ----------------------------------------------------------------------
# main command
# ----------------------------------------------------------------------
def cmd_health(args: argparse.Namespace) -> int:
    if getattr(args, "all", False):
        return cmd_health_all(args)
    light = (args.mode == "light")
    cfg = load_cfg()
    cdv_root = codev_root()

    try:
        repo = resolve_repo(args.repo)
    except RuntimeError as exc:
        out(f"error: {exc}")
        return 1

    project_id = args.project or project_id_of(repo) or "unknown"
    health = meta_health(args.project or project_id_of(repo))

    chroma_py = chroma_python()
    chroma_data = chroma_dir()
    chroma_pkg = cdv_root / "codev_platform" / "chroma"

    out(f"=== codev-platform health (mode={args.mode}) ===")
    out(f"repo: {repo}")
    out(f"project_id: {project_id}")
    if args.project:
        reg = cdv_root / "platform_meta" / "projects" / args.project
        if not reg.exists():
            out(f"project override: {args.project} (WARNING: not registered in platform_meta/projects)")
    out("")

    r = Report()
    procs = _list_processes()
    port = _daemon_port(cfg)
    model_dir = _resolve_model_dir(cfg, repo)

    _check_chroma_venv(r, chroma_py)
    _check_embed_model(r, model_dir)
    _check_embed_load(r, light, chroma_py, model_dir)
    _check_reranker(r, cfg)
    _check_torch(r, light, chroma_py)
    _check_chroma_data(r, light, chroma_py, chroma_data, chroma_pkg, project_id)
    _check_chroma_freshness(r, chroma_data, repo)
    _check_daemon(r, port)
    _check_pd_servers(r, port, procs)
    _check_mcp_proxy(r, cfg)
    _check_rules_vs_incident(r, repo)
    _check_codegraph_db(r, repo, chroma_py)
    _check_graph_store(r, project_id)
    _check_codegraph_mcp(r, repo, procs)
    _check_webhook_extra_repo_mapping(r, cfg)
    _check_reindex_worker(r)
    _check_hook_missed(r, repo, health, project_id)
    _check_git_tools(r, repo)

    r.section("")
    # 仅 --project 显式指定时按 project_id 过滤使用率; 默认 health 保持全平台合计
    # (search_recall 有 legacy 无 project_id 行, 不显式过滤避免质量基线塌掉)。
    usage_pid = args.project or None
    scope = f", project={usage_pid}" if usage_pid else ""
    r.section(f"--- usage stats (last 7 days{scope}) ---")
    from codev_platform.core.paths import logs_dir
    recall_file = logs_dir() / "search_recall.jsonl"  # 迁出包目录后与 _obslog 写入路径一致
    cg_usage = cdv_root / "codev_platform" / "codegraph" / "codegraph_usage.jsonl"
    _usage_search_recall(r, recall_file, usage_pid)
    _usage_reindex(r, repo)
    _usage_platform_docs(r, repo, recall_file, health, usage_pid)
    _usage_codegraph(r, cg_usage, usage_pid)

    # top banner (parity with .ps1 P6)
    if r.red > 0:
        out(f">>> BROKEN <<<    {r.red} FAIL / {r.amber} WARN (fix critical items below)")
    elif r.amber > 0:
        out(f">>> ATTENTION <<< all critical OK, {r.amber} WARN (degraded, still usable)")
    else:
        out(">>> READY <<<     all checks green")
    out("")

    r.flush()

    # Optional widget snapshot (parity with ai-health.ps1 -JsonOut). Runs after
    # all checks so red/amber are final; never affects the exit code below.
    if getattr(args, "json_out", None) is not None:
        if args.json_out == "":
            if project_id:
                snap = cdv_root / "platform_meta" / "health" / f"{project_id}.json"
            else:
                out("[json] WARN no project_id resolved; pass --json-out <path> explicitly")
                snap = None
        else:
            snap = Path(args.json_out).expanduser()
        if snap is not None:
            _write_json_snapshot(r, snap, project_id, args.mode)

    out("")
    if r.red > 0:
        out(f"SUMMARY: {r.red} FAIL / {r.amber} WARN")
        return 1
    if r.amber > 0:
        out(f"SUMMARY: all critical OK, {r.amber} WARN")
        return 2
    out("SUMMARY: all green")
    return 0


def _verdict(red: int, amber: int) -> str:
    if red > 0:
        return "BROKEN"
    if amber > 0:
        return "ATTENTION"
    return "READY"


def _write_json_snapshot(r: Report, path: Path, project_id: str | None, mode: str) -> None:
    """Serialise the report to a widget-readable JSON snapshot (parity with
    ai-health.ps1 -JsonOut). Non-fatal: any failure is logged, never raised."""
    try:
        checks = [
            {"tag": row["tag"], "status": row["status"], "msg": row["msg"]}
            for row in r.rows
            if "status" in row
        ]
        payload = {
            "schema_version": 1,
            "project_id": project_id,
            "mode": mode.capitalize(),
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "verdict": _verdict(r.red, r.amber),
            "fail_count": r.red,
            "warn_count": r.amber,
            "ok_count": sum(1 for c in checks if c["status"] == "OK"),
            "checks": checks,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        # UTF-8 WITHOUT BOM -- Node's JSON.parse on the widget side chokes on a BOM.
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        out(f"[json] wrote {path}")
    except Exception as exc:  # noqa: BLE001 - snapshot write must never fail health
        out(f"[json] WARN failed to write {path}: {exc}")


# ----------------------------------------------------------------------
# platform-wide aggregate (--all): HTTP client of daemon /platform/status
# 原则: 平台数据走 HTTP/HTTPS;客户端只可读取本机 env/env-file 取 Bearer token,
# 不直读平台 data/ 索引。服务端(daemon)跑在平台主机上聚合本机 data/+PG,
# 见 codev_platform/platform_status.py。
# ----------------------------------------------------------------------
def _platform_url(cfg: dict) -> str:
    base = cfg_get("platform.url", cfg=cfg) or f"http://127.0.0.1:{_daemon_port(cfg)}"
    return base.rstrip("/") + "/platform/status"


_PLATFORM_TOKEN_ENV_FALLBACKS = ("PLATFORM_TOKEN", "CODEV_PLATFORM_MCP_TOKEN")


def _platform_token_env_names(cfg: dict) -> list[str]:
    names: list[str] = []
    configured = cfg_get("platform.token_env", cfg=cfg)
    if configured:
        names.append(str(configured))
    names.extend(_PLATFORM_TOKEN_ENV_FALLBACKS)
    return list(dict.fromkeys(name for name in names if name))


def _read_env_file_values(path: str | None, allowed_names: set[str]) -> dict[str, str]:
    if not path:
        return {}
    env_path = Path(str(path)).expanduser()
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    values: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key or not key.replace("_", "").isalnum() or key[0].isdigit():
            continue
        if key not in allowed_names:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _platform_token_candidates(cfg: dict) -> list[tuple[str, str]]:
    names = _platform_token_env_names(cfg)
    candidates: list[tuple[str, str]] = []
    for env_name in names:
        token = os.environ.get(env_name)
        if token:
            candidates.append((env_name, token))
    if candidates:
        return candidates

    env_file_values = _read_env_file_values(cfg_get("systemd.env_file", cfg=cfg), set(names))
    for env_name in names:
        token = env_file_values.get(env_name)
        if token:
            candidates.append((env_name, token))
    return candidates


def _platform_status_requests(
    url: str,
    cfg: dict,
) -> list[tuple[str | None, urllib.request.Request]]:
    requests: list[tuple[str | None, urllib.request.Request]] = []
    seen_tokens: set[str] = set()
    for env_name, token in _platform_token_candidates(cfg):
        if token and token not in seen_tokens:
            seen_tokens.add(token)
            requests.append((
                env_name,
                urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}),
            ))
    if requests:
        return requests
    return [(None, urllib.request.Request(url))]


def _read_platform_status(url: str, cfg: dict) -> dict:
    last_unauthorized: urllib.error.HTTPError | None = None
    for _env_name, request in _platform_status_requests(url, cfg):
        try:
            with urllib.request.urlopen(request, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                raise
            last_unauthorized = exc
    if last_unauthorized is not None:
        raise last_unauthorized
    raise RuntimeError("no platform status request attempted")


def cmd_health_all(args: argparse.Namespace) -> int:
    cfg = load_cfg()
    url = _platform_url(cfg)
    try:
        data = _read_platform_status(url, cfg)
    except Exception as exc:  # noqa: BLE001
        out(f"[FAIL] 连不上平台服务: {url}")
        out(f"       {type(exc).__name__}: {exc}")
        if isinstance(exc, urllib.error.HTTPError) and exc.code == 401:
            envs = " / ".join(_platform_token_env_names(cfg))
            out(f"       平台服务要求 Bearer token; 请设置 {envs} 后重试。")
            out("       如需自定义变量名, 可配置 platform.token_env。")
        out("       平台数据一律走 HTTP。请确认 daemon 在跑(首个 Claude Code 会话自动起,")
        out("       或在任一仓 `codev-platform reindex` 触发);远程平台则配 config.platform.url。")
        return 1
    if isinstance(data, dict) and data.get("error"):
        out(f"[FAIL] 平台服务内部错误: {data['error']}")
        return 1

    projects = data.get("projects", {})
    mem_org = data.get("memory_org", 0)
    out("=== codev-platform health --all (平台全局视图 · via HTTP) ===")
    out(f"平台服务: {url}")
    out(f"data root: {data.get('data_root')}  |  registered: {len(data.get('registered', []))}  "
        f"|  org 共享记忆: {mem_org} 条(全项目通用)")
    out("")

    tot_chroma = 0
    for pid in sorted(projects):
        p = projects[pid]
        ch = p.get("chroma_chunks", 0)
        tot_chroma += ch
        cg = p.get("codegraph")
        if isinstance(cg, dict):
            cg_s = f"nodes={cg.get('nodes', 0)} edges={cg.get('edges', 0)} (本地 sqlite)"
        elif cg == "no_repo_path":
            cg_s = "?(仓路径未在平台登记)"
        elif cg == "no_db":
            cg_s = "无 .codegraph db"
        else:
            cg_s = str(cg)
        gr = p.get("graph")
        if isinstance(gr, dict):
            gr_s = f"nodes={gr.get('nodes', 0)} edges={gr.get('edges', 0)}"
        else:
            gr_s = "未建(跑 reindex --ingest)"
        sl = p.get("softLabels")
        if isinstance(sl, dict):
            if sl.get("domains", 0) == 0 and sl.get("layers", 0) == 0:
                sl_s = "无软层(未跑 analyzer)"
            elif sl.get("healthy"):
                sl_s = f"healthy (域 {sl.get('domains', 0)} / 层 {sl.get('layers', 0)})"
            else:
                sl_s = f"{sl.get('flags', 0)} 退化信号 (域 {sl.get('domains', 0)} / 层 {sl.get('layers', 0)})"
        else:
            sl_s = "未建"
        u = p.get("usage_7d", {})
        reg_tag = "" if p.get("registered") else "  (未注册 platform_meta)"
        out(f"[{pid}]{reg_tag}")
        out(f"    chroma 文档 = {ch} chunks")
        out(f"    codegraph 代码 = {cg_s}")
        out(f"    graph 图谱 = {gr_s}")
        out(f"    软标签 A1/A2 = {sl_s}")
        out(f"    memory 项目专属 = {p.get('memory_project', 0)} 条  (+ org 共享 {mem_org})")
        out(f"    使用率(7d) = search_docs {u.get('search_docs', 0)} / codegraph {u.get('codegraph', 0)}")
        out("")

    # MCP 端点 reachability (P5): 业务仓走服务地址连的端点是否常驻可达。
    eps = data.get("mcp_endpoints") or []
    if eps:
        out("MCP 服务端点 (业务仓走服务地址连这些):")
        for e in eps:
            mark = "OK  " if e.get("status") == "ok" else "DOWN"
            note = ""
            if e.get("status") != "ok":
                note = "  (chroma 由会话自动拉起)" if e.get("self_spawned") else "  (跑 codev-platform serve-mcp start)"
            out(f"    [{mark}] {e.get('name'):<20} :{e.get('port')}  {e.get('sse_url')}{note}")
        out("")

    proj_mem = sum(p.get("memory_project", 0) for p in projects.values())
    out(f"合计: chroma {tot_chroma} chunks / {len(projects)} 项目 ; memory {mem_org} org + {proj_mem} project")
    leg = data.get("usage_legacy")
    if leg:
        out(f"[INFO] 旧日志未带 project_id(daemon 重启后新查询才分项目): "
            f"search_docs {leg.get('search_docs', 0)} / codegraph {leg.get('codegraph', 0)}")
    for e in data.get("errors", []):
        out(f"[WARN] 服务端: {e}")
    return 0


def register(subparsers) -> None:
    sp = subparsers.add_parser("health", help="工具栈体检")
    sp.add_argument("--repo")
    sp.add_argument("--project")
    sp.add_argument("--all", action="store_true", help="平台全局视图: 所有项目 x 三库 + 记忆(不限当前仓)")
    sp.add_argument("--mode", choices=["light", "full"], default="full")
    # --json-out: write a widget-readable snapshot. Bare flag => canonical path
    # platform_meta/health/<project_id>.json; explicit path => that file.
    sp.add_argument(
        "--json-out", nargs="?", const="", default=None, dest="json_out",
        help="写健康快照 JSON (widget 读); 省略路径=写规范位置 platform_meta/health/<pid>.json",
    )
    sp.set_defaults(func=cmd_health)
