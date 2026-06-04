"""config 子命令 + 敏感值掩码 (redact_config) —— 从 cli.py 拆出。

redact_config 是纯函数, 被 tests/test_config_redact.py 直接 import (经 cli.py re-export)。
"""
from __future__ import annotations

import argparse
import json

from codev_platform.cli_cmds._shared import _eprint, _print

# key 名命中任一子串即视为敏感, 其值掩码 (大小写不敏感)。
_SECRET_KEY_HINTS = ("api_key", "apikey", "password", "passwd", "secret", "token", "dsn")


def _mask_value(key: str, value):
    """单个敏感值掩码。dsn 类只掩密码段 (复用 ops.backup._redact_dsn), 其余整体掩。

    保留前 4 位便于核对是哪把 key, 太短的整体掩。非字符串原样掩成 "***"。
    """
    if isinstance(value, str) and "dsn" in key.lower():
        from codev_platform.ops.backup import _redact_dsn
        return _redact_dsn(value)
    if not value:
        return value
    if isinstance(value, str) and len(value) > 8:
        return value[:4] + "***"
    return "***"


def redact_config(cfg):
    """纯函数: 深度遍历 dict/list, 把敏感 key 的值掩码后返回新结构, 不改原 cfg。

    敏感判定: key 名 (小写) 含 _SECRET_KEY_HINTS 任一子串。dsn 类只掩密码段。
    用于 config show/doctor --redact, 防截图 / 日志 / 备份泄漏明文 secret。
    """
    if isinstance(cfg, dict):
        out = {}
        for k, v in cfg.items():
            ks = str(k).lower()
            if any(h in ks for h in _SECRET_KEY_HINTS) and not isinstance(v, (dict, list)):
                out[k] = _mask_value(str(k), v)
            else:
                out[k] = redact_config(v)
        return out
    if isinstance(cfg, list):
        return [redact_config(it) for it in cfg]
    return cfg


def cmd_config(args: argparse.Namespace) -> int:
    """show / init / path: ~/.codev-platform/config.json 管理."""
    from codev_platform.core.config import (
        DEFAULTS, config_path, load_config, save_config,
    )
    p = config_path()
    if args.action == "path":
        _print(str(p))
        return 0
    if args.action == "show":
        cfg = load_config()
        if getattr(args, "redact", False):
            cfg = redact_config(cfg)
        _print(f"# config file: {p}  (exists={p.is_file()})")
        _print(json.dumps(cfg, ensure_ascii=False, indent=2))
        return 0
    if args.action == "doctor":
        cfg = load_config()
        shown = redact_config(cfg) if getattr(args, "redact", False) else cfg
        _print(f"# config file: {p}  (exists={p.is_file()})")
        _print(json.dumps(shown, ensure_ascii=False, indent=2))
        # provider api_key 体检: 只报 有/无, 绝不打印值
        providers = {}
        agent = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
        if isinstance(agent.get("providers"), dict):
            providers = agent["providers"]
        _print()
        _print("=== provider api_key 体检 (只报有/无, 不打印值) ===")
        if not providers:
            _print("  (无 agent.providers 配置)")
        else:
            for name, spec in providers.items():
                has = bool(isinstance(spec, dict) and spec.get("api_key"))
                _print(f"  - {name}: api_key {'有' if has else '无'}")
            _print("  建议: 定期轮换 key, 并优先用 env 引用 (env > config.agent.providers.<name>.api_key), 真 key 不进 git。")
        return 0
    if args.action == "init":
        if p.is_file() and not args.force:
            _eprint(f"已存在: {p} (加 --force 覆盖)")
            return 1
        save_config(DEFAULTS, p)
        _print(f"OK: 写入默认 config 到 {p}")
        _print("编辑后修改本机路径 (D:/models/... / data_dir 等), 或 env 临时覆盖。")
        return 0
    _eprint(f"unknown action: {args.action}")
    return 1
