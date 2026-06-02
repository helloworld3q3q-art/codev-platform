"""builtin.dotnet — 通用 .NET (C# ASP.NET) 后端栈插件 (不绑任何具体项目)。

taxonomy 定位 (Layer2 框架适配, 对齐 agent-provider-architecture 思路):
- Layer1 语言基座 = C#/.NET (文件遍历 + 稳定 node id + 解析原语)。C# 在 Python 标准库
  里没有现成 AST, 故本插件用轻量正则解析 (plan 允许"不追求覆盖所有写法"); 文件遍历 /
  相对路径 / URL 归一等**与项目无关**的原语复用 _stack_scan 的共享函数 (单一真值源)。
- Layer2 框架适配 = ASP.NET。detect 基于 **repo 内容** (存在 *.csproj 或 *.cs 含
  ASP.NET 迹象: using Microsoft.AspNetCore / [ApiController] / [HttpGet] / [Route] 等),
  不靠目录名 / 项目名, 可与其它栈共存。
- analyze 产出统一 graph/schema 的 backend_endpoint 节点 (与 FastAPI / Spring 同构)。

扫描策略 (第一版轻量):
- 控制器类: `[ApiController]` 或继承 `ControllerBase` / `Controller`; 类上的 `[Route("...")]`
  提供路由前缀 (含 `[controller]` token 用类名去 Controller 后缀替换)。
- action 方法: `[HttpGet]` / `[HttpPost]` / `[HttpPut]` / `[HttpDelete]` / `[HttpPatch]`
  (可带模板 `[HttpGet("xxx")]`) -> 一个 backend_endpoint, url = 前缀 + 方法模板拼接。
- 跨层 calls_api 边由前端插件 (frontend_react 等) 产, 本插件只产 backend 正本节点
  (同仓后端节点供前端 URL 参照)。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from codev_platform.graph.schema import (
    AnalyzerResult,
    Finding,
    GraphNode,
    NodeKind,
)
from codev_platform.plugins.base import AnalyzerPlugin
from codev_platform.plugins.builtin import _stack_scan

logger = logging.getLogger(__name__)

PLUGIN_NAME = "builtin.dotnet"

# ASP.NET 迹象字面量 (detect 用; 命中任一即认为本栈)。
_ASPNET_HINTS = (
    "Microsoft.AspNetCore",
    "[ApiController]",
    "ControllerBase",
    "IActionResult",
    "Microsoft.AspNetCore.Mvc",
)
# [Route("...")] / [HttpGet("...")] 这类带模板的路由特征 (廉价命中)。
_RE_ROUTE_ATTR = re.compile(r"\[\s*(?:Route|Http(?:Get|Post|Put|Delete|Patch))\b")

# 类声明: 取类名 + 是否 controller (名字以 Controller 结尾 / 继承 *Controller(Base))。
_RE_CLASS = re.compile(
    r"(?:public\s+|internal\s+|sealed\s+|abstract\s+|partial\s+)*class\s+"
    r"(?P<name>\w+)\s*(?::\s*(?P<bases>[\w\s,<>.]+?))?\s*\{"
)
# 任意 `class <Name>` 声明起点 (含非 controller), 用于界定 [Route] 回看窗口的类边界。
_RE_CLASS_KEYWORD = re.compile(r"\bclass\s+\w+")
# 类 / 方法上的 [Route("tmpl")] (取 tmpl 字面量; 可能有多个 attr 行)。
_RE_ROUTE_TMPL = re.compile(r"\[\s*Route\s*\(\s*\"(?P<tmpl>[^\"]*)\"")
# action 方法的 HTTP verb attribute (可选模板)。
#   容忍组合特性 (一个 [...] 内逗号分隔多个 attr, 如 [HttpGet, Produces("application/json")]):
#   匹配以 '[' 开头、内部任意 attr 前缀, 命中 Http<Verb> 及其可选 ("tmpl") 字面量。
#   `(?:[^][]*?,\s*)?` 容忍 Http 前面有其它 attr; verb 后面可继续跟逗号分隔的其它 attr 到 ']'。
_RE_HTTP_ATTR = re.compile(
    r"\[\s*(?:[^][]*?,\s*)?"
    r"Http(?P<verb>Get|Post|Put|Delete|Patch)\s*"
    r"(?:\(\s*\"(?P<tmpl>[^\"]*)\"\s*\))?"
    r"\s*(?:,[^][]*)?\]"
)
# 方法签名 (在 attribute 之后): 修饰符* 返回类型 方法名(  —— 取方法名。
_RE_METHOD = re.compile(
    r"^\s*(?:\[[^\]]*\]\s*)*"
    r"(?:public|private|protected|internal|static|async|virtual|override|\s)+"
    r"[\w<>,\[\]\.\?]+\s+(?P<name>\w+)\s*\("
)


def _is_controller(class_name: str, bases: str | None) -> bool:
    if class_name.endswith("Controller"):
        return True
    if bases and re.search(r"\bControllerBase\b|\bController\b", bases):
        return True
    return False


def _expand_route_tmpl(tmpl: str, class_name: str) -> str:
    """展开路由模板 token: [controller] -> 类名去 Controller 后缀; [action] 保留原样占位。"""
    controller = class_name
    if controller.endswith("Controller"):
        controller = controller[: -len("Controller")]
    out = tmpl.replace("[controller]", controller).replace("[Controller]", controller)
    return out


def _join_url(prefix: str, method_tmpl: str) -> str:
    """拼接类前缀与方法模板成最终 url, 归一前导斜杠 + 去重复斜杠。"""
    parts = [p.strip("/") for p in (prefix, method_tmpl) if p and p.strip("/")]
    url = "/" + "/".join(parts) if parts else "/"
    url = re.sub(r"/{2,}", "/", url)
    return _stack_scan._norm_url(url) or "/"


def dotnet_detect(repo: Path) -> bool:
    """有 .NET / ASP.NET 迹象即命中。

    1) 存在任意 *.csproj -> 是 .NET 仓 (但还需 ASP.NET 迹象才算本插件目标);
    2) 任意 *.cs 含 ASP.NET 字面量 (using Microsoft.AspNetCore / [ApiController] /
       ControllerBase / [HttpGet] / [Route(...)] 等)。

    纯按内容判定, 不看目录名 / 项目名。仅 *.csproj 而无任何 ASP.NET 迹象 (纯类库 /
    控制台) 不是本插件目标, 故不把 csproj 单独当判定依据 (只看 .cs 内容)。
    """
    for cs in _stack_scan._iter_files(repo, (".cs",)):
        try:
            text = cs.read_text(encoding="utf-8")
        except OSError:
            continue
        if any(hint in text for hint in _ASPNET_HINTS):
            return True
        if _RE_ROUTE_ATTR.search(text):
            return True

    return False


def scan_dotnet(repo: Path, project_id: str) -> tuple[list[GraphNode], list[Finding]]:
    """正则扫 C# ASP.NET 控制器 -> backend_endpoint 节点 + route 冲突 Finding。

    name = 控制器类名 + "." + action 方法名 (定位可读); url = 类 [Route] 前缀拼方法模板。
    node id 与 FastAPI 同构: "<pid>:backend_endpoint:<METHOD>:<url>"。

    去重不静默吞: 同 (METHOD, url) 多端点时只保留首个节点, 但产 1 条 Finding 暴露冲突
    (含所有冲突 handler 的 file:line), 避免 route 撞车被无声丢弃。
    """
    nodes: list[GraphNode] = []
    first_by_id: dict[str, GraphNode] = {}
    # id -> 该 id 命中的所有 (file, line, "Controller.Action") 描述, 用于冲突 Finding。
    occurrences: dict[str, list[str]] = {}

    for f in _stack_scan._iter_files(repo, (".cs",)):
        try:
            src = f.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("read fail %s: %s", f, exc)
            continue
        if "Http" not in src and "[Route" not in src:
            continue  # 廉价短路: 无 HTTP attribute 文件直接跳过。
        rel = _stack_scan._rel(f, repo)

        for node in _scan_controllers_in_file(src, rel, project_id):
            where = f"{rel}:{node.line} ({node.name})"
            occurrences.setdefault(node.id, []).append(where)
            if node.id not in first_by_id:
                first_by_id[node.id] = node
                nodes.append(node)

    findings = _build_conflict_findings(occurrences)
    return nodes, findings


def _build_conflict_findings(occurrences: dict[str, list[str]]) -> list[Finding]:
    """同一 endpoint id (METHOD+url) 命中 >1 个 handler -> 产一条 route 冲突 Finding。"""
    findings: list[Finding] = []
    for node_id, where_list in occurrences.items():
        if len(where_list) < 2:
            continue
        _, _, method, url = node_id.split(":", 3)
        logger.warning(
            "dotnet route conflict %s %s -> %s", method, url, where_list
        )
        findings.append(
            Finding(
                kind="route_conflict",
                severity="medium",
                title=f"重复路由 {method} {url} ({len(where_list)} 处)",
                detail=(
                    "同一 (HTTP method, url) 被多个 action 声明; 仅保留首个节点。"
                    " 冲突处: " + "; ".join(where_list)
                ),
                node_ids=[node_id],
                meta={
                    "http_method": method,
                    "url": url,
                    "occurrences": where_list,
                },
            )
        )
    return findings


def _scan_controllers_in_file(
    src: str, rel: str, project_id: str
) -> list[GraphNode]:
    """对单文件: 找每个 controller 类, 切出类体, 逐方法解析 HTTP verb -> endpoint。"""
    nodes: list[GraphNode] = []
    lines = src.splitlines()

    for cm in _RE_CLASS.finditer(src):
        class_name = cm.group("name")
        bases = cm.group("bases")
        if not _is_controller(class_name, bases):
            continue

        # 类上方的 attribute 块 (含 [Route]) 取路由前缀。回看窗口限定在**本类声明之前、
        # 上一个 class 声明之后**, 避免把同文件前一个 controller 的 [Route] 误派给本类。
        head_start = _class_head_start(src, cm.start())
        head = src[head_start: cm.start()]
        prefix = ""
        tmpl_m = None
        for tmpl_m in _RE_ROUTE_TMPL.finditer(head):
            pass  # 取最后一个 (最贴近类声明的) [Route]。
        if tmpl_m:
            prefix = _expand_route_tmpl(tmpl_m.group("tmpl"), class_name)

        body = _slice_class_body(src, cm.end() - 1)
        body_offset = cm.end() - 1
        nodes.extend(
            _scan_methods(
                body, body_offset, src, lines, rel, project_id,
                class_name, prefix,
            )
        )
    return nodes


def _class_head_start(src: str, class_start: int) -> int:
    """本类声明的 attribute 回看窗口起点: 上一个 `class <Name>` 声明结束处 (含其后), 否则 0。

    防止跨类串味 —— 前一个 controller 的 [Route] 不应被后一个 controller 继承。
    """
    prev_end = 0
    for km in _RE_CLASS_KEYWORD.finditer(src, 0, class_start):
        prev_end = km.end()
    return prev_end


def _slice_class_body(src: str, open_brace_idx: int) -> str:
    """从类的 '{' 起做花括号配对, 返回类体文本 (含起止括号)。失败则返回到文件末尾。"""
    depth = 0
    for i in range(open_brace_idx, len(src)):
        ch = src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[open_brace_idx: i + 1]
    return src[open_brace_idx:]


def _scan_methods(
    body: str,
    body_offset: int,
    full_src: str,
    lines: list[str],
    rel: str,
    project_id: str,
    class_name: str,
    prefix: str,
) -> list[GraphNode]:
    """在类体内: 每个 HTTP verb attribute 关联紧随其后的方法签名 -> 一个 endpoint。"""
    out: list[GraphNode] = []
    for hm in _RE_HTTP_ATTR.finditer(body):
        verb = hm.group("verb").upper()
        method_tmpl = hm.group("tmpl") or ""
        # 在 attribute 之后的窗口里找方法名 (跳过中间可能的其它 attribute 行)。
        tail = body[hm.end(): hm.end() + 400]
        name_m = None
        for line in tail.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("["):
                continue
            mm = re.search(r"\b(?P<name>\w+)\s*\(", line)
            if mm:
                name_m = mm
                break
        action = name_m.group("name") if name_m else "Action"

        url = _join_url(prefix, method_tmpl)
        abs_idx = body_offset + hm.start()
        line_no = full_src.count("\n", 0, abs_idx) + 1
        node_id = f"{project_id}:backend_endpoint:{verb}:{url}"
        out.append(
            GraphNode(
                id=node_id,
                kind=NodeKind.BACKEND_ENDPOINT.value,
                name=f"{class_name}.{action}",
                project_id=project_id,
                file=rel,
                line=line_no,
                language="csharp",
                meta={
                    "url": url,
                    "http_method": verb,
                    "handler": action,
                    "controller": class_name,
                },
            )
        )
    return out


class DotNetPlugin(AnalyzerPlugin):
    """C# ASP.NET 控制器 -> backend_endpoint 统一图谱节点。"""

    name = PLUGIN_NAME
    version = "0.1.0"

    def detect(self, repo_path: Path) -> bool:
        return dotnet_detect(Path(repo_path))

    def analyze(self, repo_path: Path, project_id: str) -> AnalyzerResult:
        repo = Path(repo_path)
        nodes, findings = scan_dotnet(repo, project_id)
        return AnalyzerResult(
            nodes=nodes,
            findings=findings,
            plugin=PLUGIN_NAME,
            plugin_version=self.version,
        )
