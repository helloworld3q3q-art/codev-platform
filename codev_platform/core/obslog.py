"""Observability-log redaction helpers — dev vs prod 两套日志规则。

三套 MCP server (chroma / cross_link / codegraph) 的 usage / recall 日志会写
query 原文、工具参数、召回文件 metadata。开发环境要全量便于排查;生产环境要脱敏
(自由文本 / 文件路径不落明文)。用户明确: 开发全 / 生产优化, 是两套规则。

设计:
- 纯函数, 不依赖任何 server 模块, 只依赖 core.config (避免 import 环 + 重依赖)。
- mode 由 config.logging.mode 决定: "dev" (默认) | "prod"。env CODEV_PLATFORM_LOG_MODE 优先覆盖。
- dev: 原样返回 (现状行为不变)。
- prod: 自由文本 / 路径替换为脱敏占位 "<redacted:len=N>"; 结构化标量 (计数 / 布尔 /
  数字 / project_id / tool 名等) 由调用方自行保留, 不传进 redact_*。

约定 (调用方遵守):
- 结构字段 (tool / project_id / elapsed_ms / ok / hit 计数 / k / rerank_used 等) 不脱敏,
  调用方直接放进 record, 不经过本模块。
- 自由文本 / 用户输入 (query) -> redact_text。
- 工具参数 dict (args, 值可能含 query / table / 路径) -> redact_args。
- 召回 metadata 里的 file 路径 -> redact_text (路径也算敏感, 暴露仓库结构)。

用法:
    from codev_platform.core.obslog import logging_mode, redact_text, redact_args
    mode = logging_mode(cfg)
    record = {"tool": name, "project_id": pid, "ok": ok,        # 结构字段原样
              "query": redact_text(query, mode),                # 自由文本脱敏
              "args": redact_args(args, mode)}                  # 参数脱敏
"""
from __future__ import annotations

import hashlib
import os
from typing import Any


_VALID_MODES = ("dev", "prod")


def logging_mode(cfg: dict[str, Any] | None = None) -> str:
    """解析当前日志模式: env CODEV_PLATFORM_LOG_MODE > config.logging.mode > 自动推断。

    自动推断 (mode 未显式设置 / 缺省 / "auto"):
    - gateway.auth_mode == "token" → "prod" (对外认证场景默认脱敏, 不漏 query 原文)。
    - 否则 → "dev" (本机 passthrough, 全量便于排查)。

    显式 logging.mode (env 或 config) 一律尊重:
    - "dev" → 即使 token 模式也保留全量 (调试逃生口)。
    - "prod" → 强制脱敏。
    任何其它非法值归一到自动推断 (避免误配把日志静默清空)。
    cfg 缺省时只看 env (调用方通常已 load_config() 后传入, 这里不强制加载避免开销)。
    """
    raw = os.environ.get("CODEV_PLATFORM_LOG_MODE")
    if not raw:
        if isinstance(cfg, dict):
            section = cfg.get("logging")
            if isinstance(section, dict):
                raw = section.get("mode")
    mode = str(raw).strip().lower() if raw else ""
    if mode in _VALID_MODES:
        return mode
    # 未显式设置 / "auto" / 非法 → 按 auth_mode 自动推断 (安全默认: token → prod)。
    if isinstance(cfg, dict):
        gateway = cfg.get("gateway")
        if isinstance(gateway, dict) and str(gateway.get("auth_mode") or "").strip().lower() == "token":
            return "prod"
    return "dev"


def _sha8(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="replace")).hexdigest()[:8]


def redact_text(s: Any, mode: str) -> Any:
    """自由文本 / 路径脱敏。

    - dev: 原样返回 (含 None / 非 str 也原样)。
    - prod: str -> "<redacted:len=N:sha8>" (保留长度 + 稳定哈希, 便于关联同一 query 而不泄漏内容);
            空串 -> "<redacted:len=0>"; None / 非 str 原样返回 (结构占位不脱敏)。
    """
    if mode != "prod":
        return s
    if s is None or not isinstance(s, str):
        return s
    if s == "":
        return "<redacted:len=0>"
    return f"<redacted:len={len(s)}:{_sha8(s)}>"


def redact_args(d: Any, mode: str) -> Any:
    """工具参数 dict 脱敏 (值可能含 query / table / 路径等自由文本)。

    - dev: 原样返回。
    - prod: 对每个 value 逐个 redact_text (str 脱敏, 非 str 原样); key 保留 (key 是
      固定参数名, 非敏感)。非 dict 入参原样返回。
    """
    if mode != "prod":
        return d
    if not isinstance(d, dict):
        return d
    return {k: redact_text(v, mode) for k, v in d.items()}
