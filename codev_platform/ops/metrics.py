"""codev_platform.ops.metrics -- aggregate observability jsonl into a summary.

  codev-platform metrics [--since 30m|2h|1d] [--json]

Reads 4 jsonl sources (already written by the running services -- we only read):
  - audit       : core.audit.audit_log_path()        ts/service/user_id/org_id/via/project_id/allowed/reason
  - chroma      : chroma/search_recall.jsonl          ts/project_id/query/hit/... (one row per search_docs)
  - cross-link  : cross_link/cross_link_usage.jsonl   ts/project_id/tool/ok/...
  - codegraph   : codegraph/codegraph_usage.jsonl     ts/project_id/tool/ok/...

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
from typing import Iterable

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


def aggregate(sources: dict[str, list[dict]]) -> MetricsSummary:
    """纯聚合: 每源指标 + 顶层 totals。源缺失 -> 视为空列表。"""
    audit = _agg_audit(sources.get("audit", []))
    chroma = _agg_recall(sources.get("chroma", []))
    cross = _agg_usage(sources.get("cross-link", []))
    codegraph = _agg_usage(sources.get("codegraph", []))
    per_source = {
        "audit": audit,
        "chroma": chroma,
        "cross-link": cross,
        "codegraph": codegraph,
    }
    totals = {
        "audit_total": audit["total"],
        "mcp_calls": chroma["total"] + cross["total"] + codegraph["total"],
        "mcp_errors": cross["errors"] + codegraph["errors"],
    }
    return MetricsSummary(per_source=per_source, totals=totals)


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
        for src in ("cross-link", "codegraph"):
            rate = summary.per_source.get(src, {}).get("error_rate", 0.0)
            if rate > err_max:
                alerts.append(f"{src} error_rate {rate:.2%} > {err_max:.2%}")
    return alerts


# --------------------------------------------------------------------------
# IO layer (thin) -- reads only, never writes the sources.
# --------------------------------------------------------------------------
def _source_paths() -> dict[str, Path]:
    """按包定位 4 个 jsonl + audit。延迟 import 避免聚合层依赖运行态。"""
    import codev_platform.chroma as _chroma
    import codev_platform.cross_link as _cl
    import codev_platform.codegraph as _cg
    from codev_platform.core.audit import audit_log_path

    chroma_dir = Path(_chroma.__file__).parent
    cl_dir = Path(_cl.__file__).parent
    cg_dir = Path(_cg.__file__).parent
    return {
        "audit": audit_log_path(),
        "chroma": chroma_dir / "search_recall.jsonl",
        "cross-link": cl_dir / "cross_link_usage.jsonl",
        "codegraph": cg_dir / "codegraph_usage.jsonl",
    }


def load_sources(now_ts: float, since_sec: float | None) -> dict[str, list[dict]]:
    """读 4 源 (缺失 -> 空 list, 不报错) + parse + within_since。"""
    out: dict[str, list[dict]] = {}
    for name, path in _source_paths().items():
        try:
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
    for src in ("chroma", "cross-link", "codegraph"):
        s = summary.per_source[src]
        if "by_tool" in s:
            print(f"{src:<11} calls={s['total']}  errors={s['errors']}  "
                  f"error_rate={s['error_rate']:.1%}")
            for tool, cnt in s["by_tool"].items():
                print(f"              {tool} x{cnt}")
        else:
            print(f"{src:<11} calls={s['total']}")
    print("-" * 48)
    print(f"totals  mcp_calls={summary.totals['mcp_calls']}  "
          f"mcp_errors={summary.totals['mcp_errors']}  "
          f"audit_total={summary.totals['audit_total']}")
    if alerts:
        print("ALERTS:")
        for msg in alerts:
            print(f"  ! {msg}")


def run_metrics(cfg, since: str | None, as_json: bool) -> int:
    since_sec = parse_since(since)
    now_ts = datetime.now(timezone.utc).timestamp()
    sources = load_sources(now_ts, since_sec)
    summary = aggregate(sources)

    from codev_platform.core.config import get
    thresholds = get(cfg, "metrics.alerts")
    alerts = check_alerts(summary, thresholds)

    if as_json:
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
        return run_metrics(load_config(), args.since, args.json)
    except ValueError as exc:
        print(f"metrics: 参数错误: {exc}", file=sys.stderr, flush=True)
        return 2


def register(subparsers) -> None:
    mp = subparsers.add_parser(
        "metrics", help="聚合可观测 jsonl (审计 + 三套 MCP 使用) 出指标摘要")
    mp.add_argument("--since", default=None,
                    help="时间窗口 (如 30m / 2h / 1d / 90s; 默认全量)")
    mp.add_argument("--json", action="store_true", help="输出 JSON 而非人类可读表")
    mp.set_defaults(func=cmd_metrics)
