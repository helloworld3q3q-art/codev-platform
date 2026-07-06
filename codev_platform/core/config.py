"""User-level config loader for machine-specific paths.

设计:
- 单一 config 文件 ~/.codev-platform/config.json
- 字段全部可选, 缺失走代码默认 hardcode
- env 变量覆盖 config (用户可在 launcher .cmd 临时强制)
- 跨项目共享 — chroma daemon / index_docs / launcher 都读它

字段:
    models.embed_path        Qwen3-Embedding 模型目录
    models.embed_device      cuda / cpu
    models.reranker_path     Qwen3-Reranker 模型目录
    models.reranker_device   cuda / cpu
    models.reranker_enabled  true / false
    data.platform_data_dir   chroma / graph 共享基目录
    mcp.platform_docs_sse_port  chroma daemon HTTP 端口 (旧 daemon.port 为兼容别名)
    daemon.mode              true=daemon 共享 / false=每会话独立
    daemon.prewarm           启动期预热模型 (避免首次 query 60s 超时)
    search.recall_k          embedding 召回候选数 (rerank 前)
    search.return_k          最终返回数
    search.bm25_enabled      RRF 融合开关

用法:
    from codev_platform.core.config import load_config, get
    cfg = load_config()
    embed_path = get(cfg, "models.embed_path", default="...")
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any


CONFIG_DIR_NAME = ".codev-platform"
CONFIG_FILE_NAME = "config.json"
DEFAULT_CONFIG_PATH = Path.home() / CONFIG_DIR_NAME / CONFIG_FILE_NAME


# 全字段默认 — config 文件 / env 未给时兜底。machine-agnostic: 模型路径默认 ~/models
# (Path.home() 各平台自解析: Win C:\Users\x\models / Mac /Users/x/models / Linux /home/x/models)。
# 换机器实际路径写进 ~/.codev-platform/config.json 覆盖, 代码里不留盘符字面量。
DEFAULTS: dict[str, Any] = {
    "models": {
        "embed_path": str(Path.home() / "models" / "Qwen3-Embedding-0.6B"),
        "embed_device": "cuda",
        "reranker_path": str(Path.home() / "models" / "Qwen3-Reranker-0.6B"),
        "reranker_device": "cuda",
        "reranker_enabled": True,
    },
    "data": {
        "platform_data_dir": None,  # None = 走 _business_repo_root cwd 推导
    },
    "daemon": {
        # 端口默认的单一真值源是 mcp_serve._bind_port (canonical 键 mcp.platform_docs_sse_port
        # > deprecated 别名 daemon.port > DEFAULT_CHROMA_PORT)。此处不再复制 daemon.port,
        # 否则默认 config 缺 canonical 键时会命中别名触发 deprecation warning。
        "mode": True,
        "prewarm": True,
    },
    "search": {
        "recall_k": 30,
        "return_k": 5,
        "bm25_enabled": True,
        "rrf_k_const": 60,
        "gpu_concurrency": 1,
    },
    "runtime": {
        "chroma_venv": None,  # chroma daemon 用的 venv 目录 (含 python.exe + mcp-proxy.exe). 必填.
    },
    "memory": {
        # 平台独立 PG 库 (codev_platform_memory) 连接串. 严禁复用业务库 DSN (见 memory plan §3.2b).
        # 密码走 env CODEV_PLATFORM_MEMORY_DSN 覆盖更安全; None = memory PG 未启用 (会话仅内存).
        "pg_dsn": None,
        # 读写分离扩展口 (预留, 不预建副本): 配只读副本 DSN 后读路径(get/has)自动走它,
        # 写路径(new/append)仍走 pg_dsn 主库. None = 读写同库 (单 PG 现状). 详见 memory plan §3.9。
        "pg_dsn_read": None,
        # M3 召回: 后端 "local"(直查 PG 结构化召回+冲突消解, 现状) | "vector"(chroma 语义排序, 数据量大时接,
        # 接缝已留见 recall_service.py). recall_limit = 注入 prompt 的最多记忆条数.
        "recall_backend": "local",
        "recall_limit": 8,
        # 冲突消解 policy (memory plan §3.5): "personal_first"(默认) | "org_first". 红线永远最高不可配.
        # M5 起改从 PG orgs.conflict_policy 读, 现为静态默认.
        "conflict_policy": "personal_first",
        # M4 压缩: 一个 (scope,topic) 攒到这么多条 active 记忆才触发 LLM 融合 (少于此不值得压).
        "compress_min_entries": 3,
        # PG 连接池每实例最大连接数. 多人并发高峰下 4 易撞 PoolTimeout; 默认 10, 按并发上调.
        "pool_max_size": 10,
    },
    "agent": {
        # 会话存储后端: "memory" (默认, 进程内, 重启丢) | "pg" (持久化到 memory.pg_dsn).
        "session_backend": "memory",
    },
    "project": {
        # Agent 项目配置查找顺序。迁移期默认 Codex 优先、Claude fallback。
        # 要切回 Claude 优先, 在 ~/.codev-platform/config.json 改为:
        # {"project": {"project_config_paths": [".claude/project.json", ".codex/project.json"]}}
        "project_config_paths": [".codex/project.json", ".claude/project.json"],
        # 文档索引配置查找顺序。避免把某个 agent 目录写死在代码里。
        "index_config_paths": [".codex/index.json", ".claude/index.json"],
    },
}


def config_path() -> Path:
    """env 可覆盖 (测试用); 默认 ~/.codev-platform/config.json"""
    return Path(os.environ.get("CODEV_PLATFORM_CONFIG", str(DEFAULT_CONFIG_PATH))).expanduser()


def load_config() -> dict[str, Any]:
    """读 config 文件, merge 进 defaults (浅合并: 顶级 key 不覆盖, 子字段合并)。

    文件不存在 -> 全部走 defaults, 不抛异常 (这是支持的 "没配就默认")。
    文件存在但 JSON 解析失败 / 读取失败 -> 默认 fail-loud (logging.error + raise),
        避免配置损坏时静默退回默认/空配置带病运行 (如 token 鉴权静默失效)。
        逃生阀: 设 CODEV_CONFIG_IGNORE_ERRORS=1 才降级为 stderr 警告 + 返默认。
    """
    cfg = {k: dict(v) if isinstance(v, dict) else v for k, v in DEFAULTS.items()}
    p = config_path()
    if not p.is_file():
        return cfg
    try:
        loaded = json.loads(p.read_text(encoding="utf-8"))
        for section, fields in loaded.items():
            if not isinstance(fields, dict):
                cfg[section] = fields
            elif section in cfg and isinstance(cfg[section], dict):
                cfg[section].update(fields)
            else:
                cfg[section] = fields
    except (json.JSONDecodeError, OSError) as exc:
        # 文件存在但损坏/不可读: 默认 fail-loud, 不静默退回默认 (防鉴权等关键配置静默失效)。
        ignore = os.environ.get("CODEV_CONFIG_IGNORE_ERRORS", "").strip().lower() in ("1", "true", "yes")
        if not ignore:
            logging.error(
                "[codev_platform.config] config 文件存在但损坏/不可读: %s (%s). "
                "拒绝带病运行; 设 CODEV_CONFIG_IGNORE_ERRORS=1 才降级返默认。",
                p, exc,
            )
            raise
        import sys
        print(
            f"[codev_platform.config] WARN: {p} 损坏或不可读, CODEV_CONFIG_IGNORE_ERRORS 已设, 全用默认值",
            file=sys.stderr, flush=True,
        )
    return cfg


def get(cfg: dict[str, Any], dotted_key: str, default: Any = None) -> Any:
    """读 'section.field' 形式的嵌套字段。"""
    cur: Any = cfg
    for part in dotted_key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def env_or_config(env_var: str, cfg: dict[str, Any], dotted_key: str, default: Any = None) -> Any:
    """env var 优先, 否则 config, 否则 default。统一 daemon / index_docs / launcher 的解析。"""
    v = os.environ.get(env_var)
    if v is not None and v != "":
        return v
    return get(cfg, dotted_key, default)


def list_env_or_config(
    env_var: str,
    cfg: dict[str, Any],
    dotted_key: str,
    default: list[str] | tuple[str, ...],
) -> list[str]:
    """从 env/config 读取字符串列表。

    env 用分隔符列表,Windows 常用 `;`,其它平台按 `os.pathsep`。JSON config 推荐用字符串数组。
    """
    raw = os.environ.get(env_var)
    if raw:
        sep = ";" if ";" in raw else os.pathsep
        return [part.strip() for part in raw.split(sep) if part.strip()]
    value = get(cfg, dotted_key, list(default))
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(part).strip() for part in value if str(part).strip()]
    return list(default)


def save_config(cfg: dict[str, Any], path: Path | None = None) -> Path:
    """保存 config 到文件 (创建父目录)。返回写入的路径。"""
    p = path or config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p
