"""codev_platform.ops.gateway —— gateway 鉴权管理 CLI 薄壳。

  gateway mode <passthrough|token>     切鉴权模式 (config.gateway.auth_mode)
  gateway token-add <user> [--org]     生成 token, 存 sha256 hash, 明文只打印这一次
  gateway token-list                   列已配 token (user/org + hash 前缀, 不含明文)
  gateway token-rm <user|hash前缀>     删 token
  gateway client-auth [--repo] [--remove] [--env PLATFORM_TOKEN]
                                       给业务仓 .mcp.json 各 server 注入/移除 Authorization header
  gateway client-url --base https://host [--repo]
                                       把 .mcp.json 各 sse server 的 url 重写成远程反代地址 (按服务名做路径前缀)

服务端逻辑复用 gateway/auth.py (token_hash + TokenAuthenticator); 本模块只做 config 管理 + .mcp.json 改写。
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from pathlib import Path


def _out(msg: str = "") -> None:
    print(msg, flush=True)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


_DURATION_UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_duration(spec: str | None) -> int | None:
    """时长串 → 秒(纯函数)。"30d"/"12h"/"90m"/"45s" → 秒;""/None → None(永久)。

    单位 d/h/m/s,前缀为正整数。非法格式抛 ValueError(调用方决定提示)。
    """
    if spec is None:
        return None
    s = spec.strip().lower()
    if s == "":
        return None
    unit = s[-1]
    if unit not in _DURATION_UNITS:
        raise ValueError(f"非法时长 {spec!r}: 单位须为 d/h/m/s (如 30d/12h/90m)")
    num = s[:-1]
    if not num.isdigit() or int(num) <= 0:
        raise ValueError(f"非法时长 {spec!r}: 须为正整数 + 单位 (如 30d)")
    return int(num) * _DURATION_UNITS[unit]


def _expires_disp(meta: dict, now: float) -> str:
    """expires_at → 人读串(剩余 / 已过期 / 永久)。"""
    exp = meta.get("expires_at")
    if exp is None or exp == "":
        return "永久"
    try:
        exp_f = float(exp)
    except (TypeError, ValueError):
        return f"{exp}(坏值, 视为已过期)"
    if exp_f < now:
        return f"已过期({int(now - exp_f)}s 前)"
    return f"{int(exp_f - now)}s 后到期"


def cmd_gateway(args: argparse.Namespace) -> int:
    from codev_platform.core.config import load_config, save_config, get
    from codev_platform.gateway.auth import token_hash

    if args.action == "mode":
        if args.arg not in ("passthrough", "token"):
            _err("FATAL: mode 取值 passthrough | token")
            return 1
        cfg = load_config()
        cfg.setdefault("gateway", {})["auth_mode"] = args.arg
        save_config(cfg)
        _out(f"OK: gateway.auth_mode = {args.arg}")
        if args.arg == "token":
            _out("提示: 重启平台服务 (sudo systemctl restart 'codev-mcp-*') 让鉴权生效;")
            _out("      客户端需带 token (gateway client-auth + export PLATFORM_TOKEN)。")
        return 0

    if args.action == "token-add":
        if not args.arg:
            _err("FATAL: token-add 需 <user>")
            return 1
        tok = secrets.token_urlsafe(32)
        h = token_hash(tok)
        cfg = load_config()
        toks = cfg.setdefault("gateway", {}).setdefault("tokens", {})
        # projects 白名单 (ACL 闸2 真值, 见 core/acl.py + gateway/auth.py):
        # "*" -> 全部; "pid1,pid2" -> list; 缺省/空 -> 不写 (安全默认: 无任何项目权)。
        raw = args.projects
        projects = None
        if raw is None or raw.strip() == "":
            _out("WARN: 未指定 --projects: token 模式下此 token 无任何项目访问权 "
                 "(用 --projects pid1,pid2 或 --projects '*')")
        elif raw.strip() == "*":
            projects = "*"
        else:
            projects = [p.strip() for p in raw.split(",") if p.strip()]
        try:
            ttl = parse_duration(getattr(args, "expires", None))
        except ValueError as exc:
            _err(f"FATAL: {exc}")
            return 1
        entry = {"user_id": args.arg, "org_id": args.org}
        if projects is not None:
            entry["projects"] = projects
        if ttl is not None:
            entry["expires_at"] = time.time() + ttl
        toks[h] = entry
        save_config(cfg)
        exp_disp = _expires_disp(entry, time.time()) if ttl is not None else "永久"
        _out("token 已生成 (明文只显示这一次, 存好):")
        _out("")
        _out(f"    {tok}")
        _out("")
        _out(f"  user_id={args.arg}  org_id={args.org}  projects={projects if projects is not None else '(无项目权)'}  expires={exp_disp}  hash={h[:12]}...")
        _out("  客户端: export PLATFORM_TOKEN='<上面 token>'  +  codev-platform gateway client-auth")
        return 0

    if args.action == "token-list":
        toks = get(load_config(), "gateway.tokens") or {}
        if not toks:
            _out("(无已配 token)")
            return 0
        now = time.time()
        _out(f"已配 {len(toks)} 个 token:")
        for h, meta in toks.items():
            proj = meta.get("projects")
            proj_disp = proj if proj is not None and proj != "" else "(无项目权)"
            _out(f"  user={meta.get('user_id')}  org={meta.get('org_id')}  projects={proj_disp}  expires={_expires_disp(meta, now)}  hash={h[:12]}...")
        return 0

    if args.action == "token-rm":
        if not args.arg:
            _err("FATAL: token-rm 需 <user 或 hash 前缀>")
            return 1
        cfg = load_config()
        toks = cfg.setdefault("gateway", {}).setdefault("tokens", {})
        victims = [h for h, m in toks.items()
                   if m.get("user_id") == args.arg or h.startswith(args.arg)]
        if not victims:
            _err(f"未找到匹配 '{args.arg}' 的 token")
            return 1
        for h in victims:
            toks.pop(h, None)
        save_config(cfg)
        _out(f"OK: 删除 {len(victims)} 个 token")
        return 0

    if args.action == "token-rotate":
        # 轮换: 给 user 生成新 token(沿用其 org/projects), 旧 token 直接删除。
        # 选"删除"而非"标过期=now": 轮换语义是替换密钥, 旧 hash 留在 config 无意义且
        # 增大泄漏面; 过期判定已能拒旧 token, 这里删干净更安全。审计留痕走 obslog, 非 config。
        if not args.arg:
            _err("FATAL: token-rotate 需 <user>")
            return 1
        cfg = load_config()
        toks = cfg.setdefault("gateway", {}).setdefault("tokens", {})
        olds = [(h, m) for h, m in toks.items() if m.get("user_id") == args.arg]
        if not olds:
            _err(f"未找到 user={args.arg!r} 的 token, 无可轮换 (先 token-add)")
            return 1
        if len(olds) > 1:
            _err(f"FATAL: user={args.arg!r} 有 {len(olds)} 个 token, 轮换目标不唯一; "
                 f"请先用 token-rm <hash前缀> 收敛到 1 个再 rotate")
            return 1
        old_h, old_meta = olds[0]
        try:
            ttl = parse_duration(getattr(args, "expires", None))
        except ValueError as exc:
            _err(f"FATAL: {exc}")
            return 1
        tok = secrets.token_urlsafe(32)
        new_h = token_hash(tok)
        new_meta = {"user_id": old_meta.get("user_id"), "org_id": old_meta.get("org_id", "default")}
        if "projects" in old_meta:
            new_meta["projects"] = old_meta["projects"]
        if ttl is not None:
            new_meta["expires_at"] = time.time() + ttl
        toks.pop(old_h, None)  # 删旧
        toks[new_h] = new_meta
        save_config(cfg)
        exp_disp = _expires_disp(new_meta, time.time())
        _out(f"token 已轮换 (旧 hash={old_h[:12]}... 已删除; 新明文只显示这一次, 存好):")
        _out("")
        _out(f"    {tok}")
        _out("")
        _out(f"  user_id={new_meta['user_id']}  org_id={new_meta['org_id']}  "
             f"projects={new_meta.get('projects', '(无项目权)')}  expires={exp_disp}  hash={new_h[:12]}...")
        _out("  客户端须更新 PLATFORM_TOKEN 为上面新 token, 旧 token 立即失效。")
        return 0

    if args.action == "client-auth":
        repo = Path(args.repo).expanduser().resolve() if args.repo else Path.cwd()
        mcp_json = repo / ".mcp.json"
        if not mcp_json.is_file():
            _err(f"FATAL: 未找到 {mcp_json}")
            return 1
        data = json.loads(mcp_json.read_text(encoding="utf-8"))
        servers = data.get("mcpServers", {})
        bearer = f"Bearer ${{{args.env}}}"
        changed = 0
        for name, conf in servers.items():
            if not isinstance(conf, dict):
                continue
            if args.remove:
                hdrs = conf.get("headers") or {}
                if "Authorization" in hdrs:
                    hdrs.pop("Authorization", None)
                    if not hdrs:
                        conf.pop("headers", None)
                    else:
                        conf["headers"] = hdrs
                    changed += 1
            else:
                conf.setdefault("headers", {})["Authorization"] = bearer
                changed += 1
        mcp_json.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        act = "移除" if args.remove else f"注入 (Authorization: {bearer})"
        _out(f"OK: {mcp_json}  {act}  —— {changed} 个 server")
        if not args.remove:
            _out(f"  客户端 shell: export {args.env}='<token>' (gateway token-add 拿到的明文), 再重启 Claude Code")
        return 0

    if args.action == "client-url":
        from urllib.parse import urlsplit, urlunsplit

        base = (args.base or "").strip()
        if not (base.startswith("http://") or base.startswith("https://")):
            _err("FATAL: client-url 需 --base https://host (必须 http(s):// 开头)")
            return 1
        base = base.rstrip("/")
        repo = Path(args.repo).expanduser().resolve() if args.repo else Path.cwd()
        mcp_json = repo / ".mcp.json"
        if not mcp_json.is_file():
            _err(f"FATAL: 未找到 {mcp_json}")
            return 1
        data = json.loads(mcp_json.read_text(encoding="utf-8"))
        servers = data.get("mcpServers", {})
        changed = 0
        for name, conf in servers.items():
            if not isinstance(conf, dict) or conf.get("type") != "sse":
                continue
            old_url = conf.get("url", "")
            # 按 server 名做路径前缀 (最稳, 不依赖原 url path 解析):
            #   platform-docs -> {base}/platform-docs/sse, cross-link -> {base}/cross-link/sse ...
            # 保留原 query (?project_id=...)。
            query = urlsplit(old_url).query if old_url else ""
            new_url = urlunsplit(("", "", f"{base}/{name}/sse", query, ""))
            conf["url"] = new_url
            _out(f"  {name}:")
            _out(f"    旧 {old_url or '(无)'}")
            _out(f"    新 {new_url}")
            changed += 1
        mcp_json.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _out(f"OK: {mcp_json}  重写 {changed} 个 sse server url -> {base}/<server>/sse")
        # 反代端口取自 config (非写死), 任何机器自洽: 默认 18xxx, 本机若改过 (如 19xxx) 这里如实打印。
        _cfg = load_config()
        _ports = {
            "platform-docs": get(_cfg, "daemon.port", 18083),
            "cross-link": get(_cfg, "mcp.cross_link_sse_port", 18086),
            "codegraph": get(_cfg, "mcp.codegraph_sse_port", 18091),
            "webhook": get(_cfg, "webhook.port", 18099),
        }
        _out("  反代须按同前缀路由到对应本机端口 (取自你的 config; 见反代 runbook):")
        for _n, _p in _ports.items():
            _out(f"    /{_n}/* -> 127.0.0.1:{_p}")
        return 0

    _err(f"unknown action: {args.action}")
    return 1


def register(subparsers) -> None:
    gw = subparsers.add_parser("gateway", help="gateway 鉴权管理 (mode / token-add / token-list / token-rm / token-rotate / client-auth / client-url)")
    gw.add_argument("action", choices=["mode", "token-add", "token-list", "token-rm", "token-rotate", "client-auth", "client-url"])
    gw.add_argument("arg", nargs="?", default=None, help="mode: passthrough|token; token-add/rm/rotate: user")
    gw.add_argument("--org", default="default", help="token-add: org_id (默认 default)")
    gw.add_argument("--projects", default=None,
                    help="token-add: 可访问项目, 逗号分隔 pid1,pid2 或 '*' 全部; 缺省=无权")
    gw.add_argument("--expires", default=None,
                    help="token-add/rotate: 有效期 30d/12h/90m/45s; 缺省/空=永久")
    gw.add_argument("--repo", default=None, help="client-auth/client-url: 业务仓路径 (默认 cwd)")
    gw.add_argument("--base", default=None, help="client-url: 远程反代基地址 (https://host)")
    gw.add_argument("--env", default="PLATFORM_TOKEN", help="client-auth: header 引用的 env 变量名")
    gw.add_argument("--remove", action="store_true", help="client-auth: 移除 Authorization header")
    gw.set_defaults(func=cmd_gateway)
