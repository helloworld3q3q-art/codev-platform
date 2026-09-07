"""文件系统工具 —— read_file / list_dir。让 agent 真读项目仓内的文件 / 看目录结构。

codegraph 只给符号片段, search_docs 只给文档块;要看某文件完整实现 / 确认"有没有前端目录"
这类全局事实, 必须能读全文 + 列目录。本工具填这个空。

**多仓**: 同一逻辑项目可跨多仓(主仓 + extra_repos, 如 PDA 前端独立仓)。沙箱允许访问**该项目
全部登记仓根**(core.repos.project_repo_roots, 与 graph ingest 同一真值源), rel 路径在各仓根依次
解析, 命中即读 —— 故 agent 能读关联仓的文件(如 PDA 页面), 而非只主仓。

沙箱铁律(安全):
- 只允许访问该项目**登记仓根内**的路径;解析后必须 is_relative_to(某仓根), 拒绝 ../ 穿越 / 绝对路径越权。
- 敏感文件(.env / *.pem / *.key / id_rsa ...)拒读 —— 防密钥被读进 LLM context 外泄(下游是第三方 provider)。
- 仓根全拿不到 → 优雅报错(不回退任意路径)。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from codev_platform.agent.brain import ToolResult
from codev_platform.agent.tools._project import resolve_project_id
from codev_platform.agent.tools.base import Tool
from codev_platform.core.repos import RepoSpec, repo_specs_from_roots, resolve_tagged_value
from codev_platform.core.repos import project_repo_roots

_MAX_BYTES = 60_000   # 单次读全文上限, 防超大文件灌爆 context
_WINDOW_LINES = 200   # 行窗口读默认行数
_WINDOW_MAX = 400     # 行窗口读单次上限(防大 limit 退化成全文)
_LIST_CAP = 200       # list_dir 单次条目上限
# 列目录时跳过的噪声目录(构建物 / 依赖 / 缓存 / 索引)
_NOISE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist", ".umi",
               ".codegraph", "target", ".pytest_cache", ".mypy_cache"}
# 敏感文件 denylist: 后缀 + basename 关键词(命中即拒读)
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore")
_SECRET_NAMES = ("id_rsa", "id_dsa", "id_ecdsa", ".env", "credentials", ".npmrc", ".pypirc")


def _resolve_specs(project_id: str | None) -> tuple[list[RepoSpec], str]:
    """该项目全部登记仓根(主仓 + extra_repos)。拿不到时返回 ([], 原因)。"""
    try:
        pid = resolve_project_id(project_id)
    except Exception as e:  # noqa: BLE001 — 工具边界, project 非法/缺失转结果
        return [], f"project 解析失败: {e}"
    roots = project_repo_roots(pid)
    if not roots:
        return [], f"project '{pid}' 仓根未知(config projects.{pid}.repo_path / meta.json 均无有效本机路径)"
    return repo_specs_from_roots(roots), ""


def _locate(specs: list[RepoSpec], rel: str, kind: str) -> tuple[Path | None, str]:
    """在**所有仓根**依次解析 rel, 返回首个落在某仓根内且为 file/dir 的 target。

    沙箱: target 必须 is_relative_to 某登记仓根(否则穿越)。全不命中时区分"越界穿越"与"不存在"。
    kind: 'file' | 'dir'。
    """
    tagged_spec, local_rel = resolve_tagged_value(specs, rel)
    if "::" in rel and tagged_spec is None:
        return None, f"未知仓 tag: {rel.split('::', 1)[0]}"
    search_specs = [tagged_spec] if tagged_spec is not None else specs
    sandbox_ok = False
    for spec in search_specs:
        root = spec.root
        try:
            target = (root / local_rel).resolve()
        except Exception:  # noqa: BLE001 — 路径非法, 试下一个仓根
            continue
        if not target.is_relative_to(root):
            continue                          # 该仓根外, 试下一个
        sandbox_ok = True                     # 至少落在某仓根内 (非穿越)
        if (kind == "file" and target.is_file()) or (kind == "dir" and target.is_dir()):
            return target, ""
    if not sandbox_ok:
        return None, "拒绝: 路径越出项目所有仓根(疑似穿越)"
    return None, (f"文件不存在: {rel}" if kind == "file" else f"目录不存在: {rel}")


def _is_secret(p: Path) -> bool:
    name = p.name.lower()
    return p.suffix.lower() in _SECRET_SUFFIXES or any(k in name for k in _SECRET_NAMES)


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "读项目仓内某文件(codegraph 只给符号片段, 看完整实现 / 配置 / 文档用它)。入参 path=相对仓根路径。"
        "**大文件优先按行窗口读**: 给 offset(起始行,1-based)+ limit(行数) 只取那一段(省 token, 返回带行号 +"
        "'用 offset=N 继续'提示); 不给 offset/limit 则读全文(≤上限)。只能读本项目仓内文件; 敏感文件(.env/密钥)拒读。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对项目仓根的文件路径"},
            "offset": {"type": "integer", "description": "起始行(1-based); 给了则按行窗口读, 配合 limit"},
            "limit": {"type": "integer", "description": f"读取行数(配合 offset, 默认 {_WINDOW_LINES}, 上限 {_WINDOW_MAX})"},
            "max_bytes": {"type": "integer", "description": f"全文读字节上限(默认 {_MAX_BYTES}; 仅不按行窗口时生效)"},
        },
        "required": ["path"],
    }

    def __init__(self, project_id: str | None = None) -> None:
        self.project_id = project_id

    def run(self, args: dict[str, Any]) -> ToolResult:
        rel = (args or {}).get("path", "").strip()
        if not rel:
            return ToolResult(call_id="", content="缺少 path 参数", is_error=True)
        specs, err = _resolve_specs(self.project_id)
        if not specs:
            return ToolResult(call_id="", content=err, is_error=True)
        target, err = _locate(specs, rel, "file")
        if target is None:
            return ToolResult(call_id="", content=err, is_error=True)
        if _is_secret(target):
            return ToolResult(call_id="", content="拒绝: 敏感文件(.env / 密钥)不可读", is_error=True)
        try:
            raw = target.read_bytes()
        except OSError as e:
            return ToolResult(call_id="", content=f"读失败: {e}", is_error=True)
        a = args or {}
        # 行窗口读(给了 offset/limit): 只取目标段, 省 token + 补"精准读片段"能力(read_file 原只能读全文)。
        if a.get("offset") is not None or a.get("limit") is not None:
            return self._read_window(rel, raw, a.get("offset"), a.get("limit"))
        # 默认: 读全文(≤ max_bytes), 行为不变。
        cap = int(a.get("max_bytes") or _MAX_BYTES)
        text = raw[:cap].decode("utf-8", errors="replace")
        trunc = f"\n…(已截断, 文件共 {len(raw)} 字节; 可用 offset/limit 按行窗口续读)" if len(raw) > cap else ""
        return ToolResult(call_id="", content=f"# {rel}\n{text}{trunc}")

    @staticmethod
    def _read_window(rel: str, raw: bytes, offset: Any, limit: Any) -> ToolResult:
        """按行窗口读: 返回 [start, start+limit) 行, 带行号 + 续读提示。"""
        lines = raw.decode("utf-8", errors="replace").splitlines()
        total = len(lines)
        try:
            start = max(1, int(offset or 1))
            n = max(1, min(int(limit or _WINDOW_LINES), _WINDOW_MAX))
        except (TypeError, ValueError):
            return ToolResult(call_id="", content="offset / limit 需为整数", is_error=True)
        window = lines[start - 1: start - 1 + n]
        end = start - 1 + len(window)
        body = "\n".join(f"{start + i}\t{ln}" for i, ln in enumerate(window))
        more = f"\n…(文件共 {total} 行; 用 offset={end + 1} 继续)" if end < total else ""
        head = f"# {rel} (行 {start}-{end} / 共 {total})"
        return ToolResult(call_id="", content=f"{head}\n{body}{more}")


class ListDirTool(Tool):
    name = "list_dir"
    description = (
        "列项目仓内某目录的文件 / 子目录(了解项目结构、确认有没有前端目录等)。"
        "入参 path=相对仓根的目录(默认仓根)。自动跳过 node_modules/.git/__pycache__ 等噪声目录。"
    )
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "相对仓根的目录(默认仓根)"}},
    }

    def __init__(self, project_id: str | None = None) -> None:
        self.project_id = project_id

    def run(self, args: dict[str, Any]) -> ToolResult:
        rel = (args or {}).get("path", "").strip() or "."
        specs, err = _resolve_specs(self.project_id)
        if not specs:
            return ToolResult(call_id="", content=err, is_error=True)
        # 多仓项目列根目录: 合并展示所有仓根(各带 header), 让 agent 看到关联仓结构(如 PDA pages/),
        # 不再因"主仓没 pages/"误判"前端不在本项目"。非根路径走单仓定位。
        if rel in (".", "") and len(specs) > 1:
            blocks = [f"# {spec.root.name}/\n{self._list_entries(spec.root)}" for spec in specs]
            return ToolResult(call_id="", content="\n\n".join(blocks))
        target, err = _locate(specs, rel, "dir")
        if target is None:
            return ToolResult(call_id="", content=err, is_error=True)
        return ToolResult(call_id="", content=self._list_entries(target) or "(空目录)")

    @staticmethod
    def _list_entries(target: Path) -> str:
        """列一个目录的条目(跳噪声目录 + 截断上限)。"""
        items = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
        lines: list[str] = []
        for e in items:
            if e.is_dir() and e.name in _NOISE_DIRS:
                continue
            lines.append(("d " if e.is_dir() else "f ") + e.name)
            if len(lines) >= _LIST_CAP:
                lines.append(f"…(超过 {_LIST_CAP} 项, 已截断)")
                break
        return "\n".join(lines)


def register_into(registry, project_id: str | None = None) -> None:
    registry.register(ReadFileTool(project_id))
    registry.register(ListDirTool(project_id))
