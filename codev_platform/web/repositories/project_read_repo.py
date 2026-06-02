"""Project 读仓 (plan §十一 CQRS read side) —— 只查询, 不写。

数据源: platform_meta/projects/<code>/meta.json 项目登记表 (与 CLI list-projects 同源,
复用 cli.PLATFORM_META_PROJECTS 路径解析, 不重写)。读 = 扫目录 + 解析 meta.json。
load/unload 运行态由 project_write_repo 的进程内 registry 持有 (第一版); PG 实现见 write repo TODO。
"""
from __future__ import annotations

import json

from codev_platform.cli import PLATFORM_META_PROJECTS


class ProjectReadRepository:
    """只读: list_projects / get_project_detail。隐藏 meta.json 文件细节。"""

    def __init__(self, meta_dir=None) -> None:
        # 默认走仓内 platform_meta (CODEV_PLATFORM_META 可覆盖); 测试可注入临时目录。
        self._meta_dir = meta_dir if meta_dir is not None else PLATFORM_META_PROJECTS

    def list_projects(self) -> list[dict]:
        """返回全部已登记项目 (按 code 排序)。无 meta 目录 → 空列表。"""
        if not self._meta_dir.is_dir():
            return []
        out: list[dict] = []
        for entry in sorted(self._meta_dir.iterdir()):
            if not entry.is_dir():
                continue
            meta = self._read_meta(entry / "meta.json", entry.name)
            if meta is not None:
                out.append(meta)
        return out

    def get_project_detail(self, code: str) -> dict | None:
        """单项目登记记录; 未登记 → None (service 转 PROJECT_UNKNOWN)。"""
        meta_file = self._meta_dir / code / "meta.json"
        if not meta_file.is_file():
            return None
        return self._read_meta(meta_file, code)

    @staticmethod
    def _read_meta(meta_file, fallback_code: str) -> dict | None:
        if not meta_file.is_file():
            return None
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return {
            "code": data.get("project_id", fallback_code),
            "name": data.get("display_name", ""),
            "repoPath": data.get("repo_path"),
            "description": data.get("notes"),
            "orgId": data.get("org_id"),
            "status": data.get("status", "ACTIVE"),
        }
