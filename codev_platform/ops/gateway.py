"""codev_platform.ops.gateway —— gateway 鉴权管理 CLI 薄壳。

  gateway mode <passthrough|token>     切鉴权模式 (config.gateway.auth_mode)
  gateway token-add <user> [--org]     生成 token, 存 sha256 hash, 明文只打印这一次
  gateway token-list                   列已配 token (user/org + hash 前缀, 不含明文)
  gateway token-rm <user|hash前缀>     删 token
  gateway client-auth [--repo] [--remove] [--env PLATFORM_TOKEN]
                                       给业务仓 .mcp.json 各 server 注入/移除 Authorization header

服务端逻辑复用 gateway/auth.py (token_hash + TokenAuthenticator); 本模块只做 config 管理 + .mcp.json 改写。
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path


def _out(msg: str = "") -> None:
    print(msg, flush=True)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


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
        entry = {"user_id": args.arg, "org_id": args.org}
        if projects is not None:
            entry["projects"] = projects
        toks[h] = entry
        save_config(cfg)
        _out("token 已生成 (明文只显示这一次, 存好):")
        _out("")
        _out(f"    {tok}")
        _out("")
        _out(f"  user_id={args.arg}  org_id={args.org}  projects={projects if projects is not None else '(无项目权)'}  hash={h[:12]}...")
        _out("  客户端: export PLATFORM_TOKEN='<上面 token>'  +  codev-platform gateway client-auth")
        return 0

    if args.action == "token-list":
        toks = get(load_config(), "gateway.tokens") or {}
        if not toks:
            _out("(无已配 token)")
            return 0
        _out(f"已配 {len(toks)} 个 token:")
        for h, meta in toks.items():
            proj = meta.get("projects")
            proj_disp = proj if proj is not None and proj != "" else "(无项目权)"
            _out(f"  user={meta.get('user_id')}  org={meta.get('org_id')}  projects={proj_disp}  hash={h[:12]}...")
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

    _err(f"unknown action: {args.action}")
    return 1


def register(subparsers) -> None:
    gw = subparsers.add_parser("gateway", help="gateway 鉴权管理 (mode / token-add / token-list / token-rm / client-auth)")
    gw.add_argument("action", choices=["mode", "token-add", "token-list", "token-rm", "client-auth"])
    gw.add_argument("arg", nargs="?", default=None, help="mode: passthrough|token; token-add/rm: user")
    gw.add_argument("--org", default="default", help="token-add: org_id (默认 default)")
    gw.add_argument("--projects", default=None,
                    help="token-add: 可访问项目, 逗号分隔 pid1,pid2 或 '*' 全部; 缺省=无权")
    gw.add_argument("--repo", default=None, help="client-auth: 业务仓路径 (默认 cwd)")
    gw.add_argument("--env", default="PLATFORM_TOKEN", help="client-auth: header 引用的 env 变量名")
    gw.add_argument("--remove", action="store_true", help="client-auth: 移除 Authorization header")
    gw.set_defaults(func=cmd_gateway)
