"""codev_platform.ops.metrics -- aggregate observability jsonl into a summary.

  codev-platform metrics [--since 30m|2h|1d] [--json]

Reads observability sources (already written by the running services -- we only read):
  - audit       : core.audit.audit_log_path()        ts/service/user_id/org_id/via/project_id/allowed/reason
  - chroma      : chroma/search_recall.jsonl          ts/project_id/query/hit/... (one row per search_docs)
  - codegraph   : codegraph/codegraph_usage.jsonl     ts/project_id/tool/ok/...
  - reindex     : tools/chroma/reindex.log            projects/scopes fan-out blocks

Pure aggregation (parse/within_since/aggregate/check_alerts) is split from the
thin IO layer (_source_paths/load_sources/run_metrics) so it is unit-testable
without touching real files. Reads only -- never writes the sources.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Iterable

_SINCE_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_since(text: str | None) -> float | None:
    """'30m'|'2h'|'1d'|'90s' -> seconds. None / '' -> None (全量)。坏值 -> ValueError。"""
    if text is None:
        return None
    text = text.strip().lower()
    if not text:
        return None
    unit = text[-1]
    if unit in _SINCE_UNITS:
        return float(text[:-1]) * _SINCE_UNITS[unit]
    return float(text)  # bare number = seconds


def parse_lines(lines: Iterable[str]) -> list[dict]:
    """逐行 json.loads, 坏行 / 非 dict 跳过 (容错: 日志可能被截断 / 并发写半行)。"""
    out: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _record_ts(rec: dict) -> float | None:
    """解析记录 ts -> epoch 秒。支持带/不带 tz 的 ISO8601。失败 -> None。"""
    raw = rec.get("ts")
    if not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def within_since(records, now_ts: float, since_sec: float | None) -> list[dict]:
    """按 ts 过滤近 since_sec 秒内的记录。

    - since None -> 全量返回 (不过滤)。
    - ts 缺失 / 不可解析 -> 保留 (宁可多算也不静默丢观测点)。
    - now 入参保确定性 (可测)。
    """
    if since_sec is None:
        return list(records)
    cutoff = now_ts - since_sec
    out: list[dict] = []
    for rec in records:
        t = _record_ts(rec)
        if t is None or t >= cutoff:
            out.append(rec)
    return out


@dataclass
class MetricsSummary:
    per_source: dict = field(default_factory=dict)
    totals: dict = field(default_factory=dict)
    flags: list = field(default_factory=list)


def _agg_audit(records: list[dict]) -> dict:
    allowed = sum(1 for r in records if r.get("allowed"))
    denied = len(records) - allowed
    deny_reasons = Counter(
        str(r.get("reason")) for r in records if not r.get("allowed"))
    return {
        "total": len(records),
        "allowed": allowed,
        "denied": denied,
        "deny_rate": (denied / len(records)) if records else 0.0,
        "deny_by_reason": dict(deny_reasons.most_common(5)),
    }


def _agg_usage(records: list[dict]) -> dict:
    by_tool = Counter(str(r.get("tool")) for r in records if r.get("tool"))
    errors = sum(1 for r in records if r.get("ok") is False)
    return {
        "total": len(records),
        "errors": errors,
        "error_rate": (errors / len(records)) if records else 0.0,
        "by_tool": dict(by_tool.most_common()),
    }


def _agg_recall(records: list[dict]) -> dict:
    return {"total": len(records)}  # 召回行无 ok/tool, 只计调用次数


def parse_reindex_log(text: str) -> list[dict]:
    """解析 tools/chroma/reindex.log 的轻量结构, 提取每次入队的 fan-out 规模。

    日志是人类可读文本, 这里只读 `reindex started at` / `projects:` / `scopes:` 三类稳定行。
    """
    records: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("===== reindex started at "):
            if cur is not None:
                records.append(cur)
            raw = line.removeprefix("===== reindex started at ").removesuffix(" =====").strip()
            cur = {"ts": raw, "projects": [], "scopes": []}
            continue
        if cur is None:
            continue
        if line.startswith("projects:"):
            cur["projects"] = [p.strip() for p in line.partition(":")[2].split(",") if p.strip()]
        elif line.startswith("scopes:"):
            cur["scopes"] = [s.strip() for s in line.partition(":")[2].split(",") if s.strip()]
    if cur is not None:
        records.append(cur)
    for rec in records:
        rec["fanout_projects"] = len(rec.get("projects") or [])
        rec["fanout_scopes"] = len(rec.get("scopes") or [])
    return records


def _agg_reindex(records: list[dict]) -> dict:
    fanout = [r for r in records if int(r.get("fanout_projects") or 0) > 1]
    max_projects = max((int(r.get("fanout_projects") or 0) for r in records), default=0)
    max_scopes = max((int(r.get("fanout_scopes") or 0) for r in records), default=0)
    by_scope: Counter[str] = Counter()
    for r in records:
        for scope in r.get("scopes") or []:
            by_scope[str(scope)] += 1
    return {
        "total": len(records),
        "fanout_runs": len(fanout),
        "max_projects": max_projects,
        "max_scopes": max_scopes,
        "by_scope": dict(by_scope.most_common()),
    }


def aggregate(sources: dict[str, list[dict]]) -> MetricsSummary:
    """纯聚合: 每源指标 + 顶层 totals。源缺失 -> 视为空列表。"""
    audit = _agg_audit(sources.get("audit", []))
    chroma = _agg_recall(sources.get("chroma", []))
    codegraph = _agg_usage(sources.get("codegraph", []))
    reindex = _agg_reindex(sources.get("reindex", []))
    per_source = {
        "audit": audit,
        "chroma": chroma,
        "codegraph": codegraph,
        "reindex": reindex,
    }
    totals = {
        "audit_total": audit["total"],
        "mcp_calls": chroma["total"] + codegraph["total"],
        "mcp_errors": codegraph["errors"],
        "reindex_runs": reindex["total"],
        "reindex_fanout_runs": reindex["fanout_runs"],
    }
    return MetricsSummary(per_source=per_source, totals=totals)


def _prom_escape(value: str) -> str:
    """Prometheus label value escaping: backslash / quote / newline。"""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def to_prometheus(summary: MetricsSummary) -> str:
    """把 summary 转 Prometheus 文本曝光格式 (供 node_exporter textfile / cron 抓)。

    纯字符串, 无 IO, 可测。每个 metric 带 # HELP / # TYPE。
    """
    lines: list[str] = []

    def metric(name: str, mtype: str, help_text: str, samples: list[tuple[str, float]]):
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {mtype}")
        for labels, val in samples:
            suffix = "{" + labels + "}" if labels else ""
            v = int(val) if float(val).is_integer() else val
            lines.append(f"{name}{suffix} {v}")

    audit = summary.per_source.get("audit", {})
    metric("codev_audit_requests_total", "gauge", "audit log entries in window", [
        ('result="allowed"', audit.get("allowed", 0)),
        ('result="denied"', audit.get("denied", 0)),
    ])
    metric("codev_audit_deny_rate", "gauge", "audit deny rate in window",
           [("", audit.get("deny_rate", 0.0))])
    for reason, cnt in audit.get("deny_by_reason", {}).items():
        lines.append(f'codev_audit_deny_by_reason{{reason="{_prom_escape(reason)}"}} {cnt}')

    call_samples: list[tuple[str, float]] = []
    err_samples: list[tuple[str, float]] = []
    rate_samples: list[tuple[str, float]] = []
    for src in ("chroma", "codegraph"):
        s = summary.per_source.get(src, {})
        label = f'service="{_prom_escape(src)}"'
        call_samples.append((label, s.get("total", 0)))
        if "error_rate" in s:
            err_samples.append((label, s.get("errors", 0)))
            rate_samples.append((label, s.get("error_rate", 0.0)))
    metric("codev_mcp_calls_total", "gauge", "MCP calls per service in window", call_samples)
    metric("codev_mcp_errors_total", "gauge", "MCP errors per service in window", err_samples)
    metric("codev_mcp_error_rate", "gauge", "MCP error rate per service in window", rate_samples)

    reindex = summary.per_source.get("reindex", {})
    metric("codev_reindex_runs_total", "gauge", "reindex runs in window",
           [("", reindex.get("total", 0))])
    metric("codev_reindex_fanout_runs_total", "gauge", "reindex runs targeting multiple projects",
           [("", reindex.get("fanout_runs", 0))])
    metric("codev_reindex_fanout_max_projects", "gauge", "max project fan-out per reindex run",
           [("", reindex.get("max_projects", 0))])
    metric("codev_reindex_fanout_max_scopes", "gauge", "max scope fan-out per reindex run",
           [("", reindex.get("max_scopes", 0))])
    for scope, cnt in reindex.get("by_scope", {}).items():
        lines.append(f'codev_reindex_runs_by_scope{{scope="{_prom_escape(scope)}"}} {cnt}')

    if summary.flags:
        metric("codev_alert", "gauge", "active alert flags (1=firing)",
               [(f'name="{_prom_escape(str(f))}"', 1) for f in summary.flags])

    return "\n".join(lines) + "\n"


def check_alerts(summary: MetricsSummary, thresholds: dict | None) -> list[str]:
    """简单阈值告警。thresholds 缺省 / 空 -> 不告警 (宽松)。"""
    alerts: list[str] = []
    th = thresholds or {}
    deny_max = th.get("deny_rate_max")
    if deny_max is not None:
        rate = summary.per_source.get("audit", {}).get("deny_rate", 0.0)
        if rate > deny_max:
            alerts.append(f"audit deny_rate {rate:.2%} > {deny_max:.2%}")
    err_max = th.get("error_rate_max")
    if err_max is not None:
        for src in ("codegraph",):
            rate = summary.per_source.get(src, {}).get("error_rate", 0.0)
            if rate > err_max:
                alerts.append(f"{src} error_rate {rate:.2%} > {err_max:.2%}")
    fanout_projects_max = th.get("reindex_fanout_projects_max")
    if fanout_projects_max is not None:
        got = summary.per_source.get("reindex", {}).get("max_projects", 0)
        if got > fanout_projects_max:
            alerts.append(f"reindex fanout projects {got} > {fanout_projects_max}")
    fanout_scopes_max = th.get("reindex_fanout_scopes_max")
    if fanout_scopes_max is not None:
        got = summary.per_source.get("reindex", {}).get("max_scopes", 0)
        if got > fanout_scopes_max:
            alerts.append(f"reindex fanout scopes {got} > {fanout_scopes_max}")
    return alerts


def deliver_alerts(alerts: list[str], cfg) -> None:
    """投递告警: ① 每条永远 _log 一条 WARN; ② config metrics.alert_webhook 非空则 POST。

    webhook POST 是 IO 薄层: urllib + 短超时, 任何失败静默 (不阻塞巡检主流程)。
    无告警 -> 直接返回。
    """
    if not alerts:
        return
    import logging
    log = logging.getLogger("codev_platform.ops.metrics")
    for msg in alerts:
        log.warning("metrics alert: %s", msg)

    from codev_platform.core.config import get
    webhook = get(cfg, "metrics.alert_webhook")
    if not webhook:
        return
    try:
        import urllib.request
        payload = json.dumps({"alerts": alerts}).encode("utf-8")
        req = urllib.request.Request(
            webhook, data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=3).close()
    except Exception:  # noqa: BLE001 -- 投递失败不阻塞巡检
        log.warning("metrics alert webhook delivery failed (ignored)")


# --------------------------------------------------------------------------
# IO layer (thin) -- reads only, never writes the sources.
# --------------------------------------------------------------------------
def _source_paths() -> dict[str, Path]:
    """按包定位 jsonl + audit。延迟 import 避免聚合层依赖运行态。"""
    import codev_platform.codegraph as _cg
    from codev_platform.core.audit import audit_log_path

    from codev_platform.core.paths import logs_dir
    cg_dir = Path(_cg.__file__).parent
    return {
        "audit": audit_log_path(),
        # search_recall.jsonl 已迁出包目录到 data_root/logs (与 _obslog 写入路径一致);
        # codegraph 的 usage.jsonl 未迁移, 仍在包目录。
        "chroma": logs_dir() / "search_recall.jsonl",
        "codegraph": cg_dir / "codegraph_usage.jsonl",
        "reindex": Path.cwd() / "tools" / "chroma" / "reindex.log",
    }


def load_sources(now_ts: float, since_sec: float | None) -> dict[str, list[dict]]:
    """读 4 源 (缺失 -> 空 list, 不报错) + parse + within_since。"""
    out: dict[str, list[dict]] = {}
    for name, path in _source_paths().items():
        try:
            if name == "reindex":
                recs = parse_reindex_log(path.read_text(encoding="utf-8", errors="replace"))
            else:
                with path.open("r", encoding="utf-8") as f:
                    recs = parse_lines(f)
        except FileNotFoundError:
            recs = []
        except Exception:  # noqa: BLE001 -- 一源坏不拖垮整体
            recs = []
        out[name] = within_since(recs, now_ts, since_sec)
    return out


def _print_human(summary: MetricsSummary, alerts: list[str], since: str | None) -> None:
    scope = since if since else "all"
    print(f"codev-platform metrics  (window: {scope})")
    print("-" * 48)
    a = summary.per_source["audit"]
    print(f"audit       total={a['total']}  allowed={a['allowed']}  "
          f"denied={a['denied']}  deny_rate={a['deny_rate']:.1%}")
    for reason, cnt in a["deny_by_reason"].items():
        print(f"              deny: {reason} x{cnt}")
    for src in ("chroma", "codegraph"):
        s = summary.per_source[src]
        if "by_tool" in s:
            print(f"{src:<11} calls={s['total']}  errors={s['errors']}  "
                  f"error_rate={s['error_rate']:.1%}")
            for tool, cnt in s["by_tool"].items():
                print(f"              {tool} x{cnt}")
        else:
            print(f"{src:<11} calls={s['total']}")
    ri = summary.per_source["reindex"]
    print(f"reindex    runs={ri['total']}  fanout_runs={ri['fanout_runs']}  "
          f"max_projects={ri['max_projects']}  max_scopes={ri['max_scopes']}")
    print("-" * 48)
    print(f"totals  mcp_calls={summary.totals['mcp_calls']}  "
          f"mcp_errors={summary.totals['mcp_errors']}  "
          f"audit_total={summary.totals['audit_total']}")
    if alerts:
        print("ALERTS:")
        for msg in alerts:
            print(f"  ! {msg}")


def run_metrics(cfg, since: str | None, as_json: bool,
                as_prometheus: bool = False) -> int:
    since_sec = parse_since(since)
    now_ts = datetime.now(timezone.utc).timestamp()
    sources = load_sources(now_ts, since_sec)
    summary = aggregate(sources)

    from codev_platform.core.config import get
    thresholds = get(cfg, "metrics.alerts")
    alerts = check_alerts(summary, thresholds)
    summary.flags = alerts

    deliver_alerts(alerts, cfg)

    if as_prometheus:
        print(to_prometheus(summary), end="")
    elif as_json:
        payload = {
            "since": since,
            "per_source": summary.per_source,
            "totals": summary.totals,
            "alerts": alerts,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_human(summary, alerts, since)
    return 1 if alerts else 0


def cmd_metrics(args: argparse.Namespace) -> int:
    from codev_platform.core.config import load_config
    try:
        return run_metrics(load_config(), args.since, args.json, args.prometheus)
    except ValueError as exc:
        print(f"metrics: 参数错误: {exc}", file=sys.stderr, flush=True)
        return 2


def register(subparsers) -> None:
    mp = subparsers.add_parser(
        "metrics", help="聚合可观测 jsonl (审计 + 三套 MCP 使用) 出指标摘要")
    mp.add_argument("--since", default=None,
                    help="时间窗口 (如 30m / 2h / 1d / 90s; 默认全量)")
    mp.add_argument("--json", action="store_true", help="输出 JSON 而非人类可读表")
    mp.add_argument("--prometheus", action="store_true",
                    help="输出 Prometheus 曝光文本 (供 node_exporter textfile / cron 抓)")
    mp.set_defaults(func=cmd_metrics)
