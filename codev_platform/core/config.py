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
    data.platform_data_dir   chroma / cross_link 共享基目录
    daemon.port              chroma daemon HTTP 端口
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
import os
from pathlib import Path
from typing import Any


CONFIG_DIR_NAME = ".codev-platform"
CONFIG_FILE_NAME = "config.json"
DEFAULT_CONFIG_PATH = Path.home() / CONFIG_DIR_NAME / CONFIG_FILE_NAME


# 全字段默认 — config 文件 / env 未给时兜底 (machine-agnostic 跨开发机可改)
DEFAULTS: dict[str, Any] = {
    "models": {
        "embed_path": r"D:\models\Qwen3-Embedding-0.6B",
        "embed_device": "cuda",
        "reranker_path": r"D:\models\Qwen3-Reranker-0.6B",
        "reranker_device": "cuda",
        "reranker_enabled": True,
    },
    "data": {
        "platform_data_dir": None,  # None = 走 _business_repo_root cwd 推导
    },
    "daemon": {
        "port": 18083,
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
}


def config_path() -> Path:
    """env 可覆盖 (测试用); 默认 ~/.codev-platform/config.json"""
    return Path(os.environ.get("CODEV_PLATFORM_CONFIG", str(DEFAULT_CONFIG_PATH))).expanduser()


def load_config() -> dict[str, Any]:
    """读 config 文件, merge 进 defaults (浅合并: 顶级 key 不覆盖, 子字段合并)。

    文件不存在或损坏 -> 全部走 defaults, 不抛异常 (保证调用方不需要 try)。
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
    except (json.JSONDecodeError, OSError):
        # 容错: 损坏的 config 不破坏 daemon, stderr 警告即可
        import sys
        print(
            f"[codev_platform.config] WARN: {p} 损坏或不可读, 全用默认值",
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


def save_config(cfg: dict[str, Any], path: Path | None = None) -> Path:
    """保存 config 到文件 (创建父目录)。返回写入的路径。"""
    p = path or config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p
