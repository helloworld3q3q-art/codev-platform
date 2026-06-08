"""Project 写仓 (plan §十一 CQRS write side) —— 只创建/更新, 不查询业务流程。

register: 写 platform_meta/projects/<code>/meta.json (唯一约束 = 目录已存在则视为已注册,
幂等由 service 先经 read repo 查重把关, 此处再用 exclusive 写兜底)。
load/unload: 进程内 _LOADED registry 持有运行态。**这是设计契约不是缺陷** —— loaded 是纯派生
UI 标记(项目是否已加载), 无业务数据; 重启丢失只需重新 load, 多 worker 各看各的只是显示抖动。
故**不为它建 PG 表**(为纯 UI 标记建表/迁移/回退 = 过度工程)。

# 决策(codex audit P3 / 2026-06-08):
# - _LOADED 维持进程内 + 明确"不持久/不跨实例"契约, 不建表(纯 UI 标记, 跨实例显示 loaded
#   才需要时再补 project_runtime 表)。
# - ⚠️ 真正的多实例隐患是 web/domain/locks.py 的 ProjectLockRegistry(进程内互斥锁): 多 worker
#   下互斥失效 → 同项目可被并发提交重建、写坏 codegraph/chroma 索引。**上多实例前必做** ——
#   换 Redis / 文件锁(对齐 core spawn_lock)。比 _LOADED 优先级高, 在此登记防隐身。
# - register 已落 meta.json(read repo 也从文件读, 不丢); register → projects 表(PG) 是多租户
#   存储统一的优化, 可与 jobs PG 化同批做, 非数据丢失修复。
"""
from __future__ import annotations

import json

from codev_platform.cli import PLATFORM_META_PROJECTS
from codev_platform.core.errors import ErrorCode, PlatformError

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
                         repo_path: str | None, description: str | None,
                         org_id: str | None = None) -> dict:
        """写 meta.json (创建项目登记)。调用方 (service) 已保证 code 未占用。

        org_id 为项目归属组织 (None = 公开, 全 org 可见; 与 list 过滤同口径)。
        """
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
        if org_id:
            meta["org_id"] = org_id
        content = json.dumps(meta, ensure_ascii=False, indent=2) + "\n"
        try:
            with open(target, "x", encoding="utf-8") as f:
                f.write(content)
        except FileExistsError as exc:
            raise PlatformError(ErrorCode.INVALID_PARAMS, f"project already registered: {code}") from exc
        return {
            "code": code, "name": name, "repoPath": repo_path,
            "description": description, "orgId": org_id, "status": "ACTIVE",
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
