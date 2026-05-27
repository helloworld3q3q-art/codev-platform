"""project_id resolver for multi-project AI tooling.

支持两种解析模式 (本地原型 + server 部署共用):
- resolve_local(): launcher / CLI / index script 在本机跑
    优先级: env PLATFORM_PROJECT_ID > .claude/project.json > 硬失败
- resolve_from_request(headers): daemon HTTP handler 处理 client 请求
    优先级: X-Project-Id header > 硬失败 (调用方转 400)

格式约束 (跨 chroma collection / 目录名 / URL 安全):
- 小写字母 / 数字 / 连字符
- 1-64 字符
- 首字符必须字母数字 (不允许 - 开头, 防 CLI 参数歧义)
- 不允许斜杠 (路径污染) / 下划线 (与 chroma collection 分隔符 `__` 冲突)
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path


ENV_VAR = "PLATFORM_PROJECT_ID"
CONFIG_RELPATH = ".claude/project.json"
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class ProjectIdError(RuntimeError):
    """project_id 缺失 / 格式非法 / 配置文件解析失败。"""


def validate(project_id: str) -> str:
    """格式校验, 返回归一化后的值。非法抛 ProjectIdError。"""
    if project_id is None:
        raise ProjectIdError("project_id 不能为空")
    normalized = str(project_id).strip().lower()
    if not normalized:
        raise ProjectIdError("project_id 不能为空白字符串")
    if not _SLUG_RE.match(normalized):
        raise ProjectIdError(
            f"project_id 格式非法: {project_id!r} "
            "(要求: 小写字母/数字/连字符, 1-64 字符, 首字符字母数字; "
            "不允许 _ / 空格)"
        )
    return normalized


def _read_repo_config(start: Path) -> tuple[str, Path] | None:
    """从 start 目录向上查找 .claude/project.json, 返回 (project_id, file_path)。"""
    current = start.resolve()
    for parent in [current, *current.parents]:
        candidate = parent / CONFIG_RELPATH
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ProjectIdError(
                    f"{candidate} JSON 解析失败: {exc!s}"
                ) from exc
            pid = data.get("project_id")
            if pid:
                return str(pid), candidate
    return None


def resolve_local(cwd: Path | None = None) -> str:
    """client 端解析。返回归一化后的 project_id, 失败抛 ProjectIdError 含修复指引。"""
    env_value = os.environ.get(ENV_VAR)
    if env_value:
        return validate(env_value)

    start = cwd or Path.cwd()
    found = _read_repo_config(start)
    if found is not None:
        pid, _path = found
        return validate(pid)

    raise ProjectIdError(
        "无法解析 project_id。请二选一:\n"
        f"  (a) 设环境变量: $env:{ENV_VAR}='<your-project-id>'  (PowerShell)\n"
        f"                  set {ENV_VAR}=<your-project-id>      (cmd)\n"
        f"  (b) 在仓库根创建 {CONFIG_RELPATH}: "
        '{"project_id": "<your-project-id>"}\n'
        "  project_id 格式: 小写字母/数字/连字符 (例 openclaw-stock)"
    )


def resolve_from_request(headers) -> str:
    """server 端解析: X-Project-Id header 必填。缺失抛 ProjectIdError (调用方转 400)。

    headers 接受 dict / Mapping / starlette Headers, 大小写完全不敏感
    (生产 nginx / CDN 可能 normalize 成全小写或混合大小写, 不能假设固定形式)。
    """
    pid = None
    # 优先走原生 get (starlette Headers 自带大小写不敏感)
    if hasattr(headers, "get"):
        for key in ("X-Project-Id", "x-project-id", "X-PROJECT-ID"):
            v = headers.get(key)
            if v:
                pid = v
                break
    # 兜底: 把 headers 转 dict, 全 key 小写后查
    if not pid:
        try:
            items = headers.items() if hasattr(headers, "items") else iter(headers)
            for k, v in items:
                if str(k).lower() == "x-project-id" and v:
                    pid = v
                    break
        except Exception:
            pass
    if not pid:
        raise ProjectIdError("缺少 X-Project-Id header (server 模式必填)")
    return validate(pid)
