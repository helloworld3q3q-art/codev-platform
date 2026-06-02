"""Project 写仓 (plan §十一 CQRS write side) —— 只创建/更新, 不查询业务流程。

register: 写 platform_meta/projects/<code>/meta.json (唯一约束 = 目录已存在则视为已注册,
幂等由 service 先经 read repo 查重把关, 此处再用 exclusive 写兜底)。
load/unload: 第一版用进程内 _LOADED registry 持有运行态 (单进程互斥, 对齐 plan §十二
"in-process project lock registry")。PG 实现可按现有 *_store_pg 范式补 (TODO 见下)。

# TODO(PG): register → projects 表 upsert (唯一约束 code); load 状态 → project_runtime 表,
#   对齐 memory_store_pg / rbac_store_pg 落 PG, 多租户存储不分叉 (plan D5)。第一版文件/内存先行。
"""
from __future__ import annotations

import json

from codev_platform.cli import PLATFORM_META_PROJECTS

# 进程内运行态: 已 load 的 project code 集合 (单进程互斥; 跨进程升级见模块 docstring TODO)。
_LOADED: set[str] = set()


class ProjectWriteRepository:
    """只写: register_project / set_loaded。唯一约束 + 幂等由文件存在性兜底。"""

    def __init__(self, meta_dir=None) -> None:
        self._meta_dir = meta_dir if meta_dir is not None else PLATFORM_META_PROJECTS

    def exists(self, code: str) -> bool:
        """唯一约束探测: meta.json 已存在 = 已注册 (供 service 查重)。"""
        return (self._meta_dir / code / "meta.json").is_file()

    def register_project(self, *, code: str, name: str,
                         repo_path: str | None, description: str | None) -> dict:
        """写 meta.json (创建项目登记)。调用方 (service) 已保证 code 未占用。"""
        target = self._meta_dir / code / "meta.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "project_id": code,
            "display_name": name,
            "repo_path": repo_path,
            "rules_path": ".claude/rules",
            "memory_path": "memory",
            "status": "ACTIVE",
        }
        if description:
            meta["notes"] = description
        target.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {
            "code": code, "name": name, "repoPath": repo_path,
            "description": description, "status": "ACTIVE",
        }

    @staticmethod
    def set_loaded(code: str, loaded: bool) -> bool:
        """置项目运行态 (进程内)。返回最终 loaded 标志 (幂等: 重复 load/unload 无副作用)。"""
        if loaded:
            _LOADED.add(code)
        else:
            _LOADED.discard(code)
        return code in _LOADED

    @staticmethod
    def is_loaded(code: str) -> bool:
        return code in _LOADED
