"""前端 API URL 注册文件提取(代码基础层)—— 从"集中声明 API 路径的常量文件"抽 URL。

**为什么需要**: 企业前端常不直接写 axios/fetch('/url'), 而是把 API 路径集中声明成常量
(`export const CURRENCY_LIST = '/pda/currency/list'`), 业务代码引用常量名调用。通用 scanner
只认内联字面量 → 这类项目前端 API 调用几乎全漏 → 前后端图谱连不上。

**通用 + 低耦合 + 可扩展**(不绑任何项目):
- 识别注册文件: ① 项目声明(declared_files)优先 ② 否则启发式(.js/.ts 文件含 ≥min 个
  API 路径字面量即认作注册文件)—— 不硬编码 URL.js 文件名。
- 解析: const 赋值 + **同文件内 `BASE + 'suffix'` 拼接解析**(URL.js 常见 `SUFFIX + 'login'`)。
- 归一化: 剥 ?query / 模板参 {0}/${x} / 尾部 = 与 / → 裸路径(与后端端点路径同形, _link 可匹配)。
- 只产 frontend_api_call 节点; URL 匹配后端走 _link.py(仓库无关), 不在此建边。

这是"代码为基础"的静态层; 静态 resolve 不了的(运行时计算 URL / 函数拼接)留给 LLM analyzer 补充。
"""
from __future__ import annotations

import re
from pathlib import Path

from codev_platform.graph.schema import GraphNode, NodeKind

from ._common import _iter_files, _rel

# const/let/var 赋值; 值取到第一个 `;`(允许跨行, [^;] 含换行)。
_RE_CONST = re.compile(r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*([^;]+);")
# 字符串字面量(单/双/反引号)。
_RE_STR = re.compile(r"""['"`]([^'"`]*)['"`]""")
# API 路径: 以 / 开头紧跟字母(排除 // 注释 / 协议 // / 相对路径)。
_API_PATH = re.compile(r"^/[A-Za-z]")

_MIN_PATHS_FOR_REGISTRY = 8  # 文件内 API 路径字面量 ≥ 此数 → 认作 URL 注册文件(启发式)


def _normalize_path(raw: str) -> str:
    """声明的 URL → 可匹配后端的裸路径: 剥 ?query / #frag / 模板参 / 尾部 = 与 /。
    非 API 路径(不以 /字母 开头)→ ""(调用方丢弃)。"""
    s = raw.split("?", 1)[0].split("#", 1)[0].strip()
    s = re.sub(r"\$?\{[^}]*\}", "", s)   # 剥 {0} / ${x} 模板参
    s = s.rstrip("=").rstrip("/")
    return s if _API_PATH.match(s) else ""


def _resolve_consts(text: str) -> dict[str, str]:
    """解析文件内 const 字符串值, 含同文件 `BASE + 'lit'` 拼接的引用解析。name -> 值(尽力)。

    值表达式按 `+` 分段, 每段是字符串字面量或已知 const 名(递归解析); 含变量/函数调用的段
    无法静态 resolve → 整条放弃(不猜)。
    """
    raw: dict[str, str] = {m.group(1): m.group(2).strip() for m in _RE_CONST.finditer(text)}

    def _eval(expr: str, depth: int = 0) -> str | None:
        if depth > 6:
            return None
        parts = [p.strip() for p in expr.split("+")]
        out: list[str] = []
        for p in parts:
            sm = _RE_STR.fullmatch(p)
            if sm:
                out.append(sm.group(1))
            elif p in raw:
                sub = _eval(raw[p], depth + 1)
                if sub is None:
                    return None
                out.append(sub)
            else:
                return None  # 含变量/函数/计算 → resolve 不了
        return "".join(out)

    resolved: dict[str, str] = {}
    for name, expr in raw.items():
        v = _eval(expr)
        if v is not None:
            resolved[name] = v
    return resolved


def _count_api_paths(text: str) -> int:
    return sum(1 for m in _RE_STR.finditer(text) if _API_PATH.match(m.group(1).split("?")[0]))


def scan_url_registry(
    repo: Path, project_id: str, *,
    declared_files: list[str] | None = None,
    min_paths: int = _MIN_PATHS_FOR_REGISTRY,
) -> list[GraphNode]:
    """从 URL 注册文件抽 API 路径 → frontend_api_call 节点。

    declared_files(相对 repo, 项目声明)优先; 否则启发式扫 .js/.ts 找高密度 API 路径文件。
    method 未知(注册文件不声明动词)→ 默认 POST, meta.url_registry=True(_link URL 匹配为主)。
    """
    if declared_files:
        files = [repo / f for f in declared_files if (repo / f).is_file()]
    else:
        files = [f for f in _iter_files(repo, (".js", ".ts"))
                 if _count_api_paths(_safe_read(f)) >= min_paths]

    nodes: list[GraphNode] = []
    seen: set[str] = set()
    for f in files:
        text = _safe_read(f)
        if not text:
            continue
        rel = _rel(f, repo)
        for name, val in _resolve_consts(text).items():
            path = _normalize_path(val)
            if not path:
                continue
            node_id = f"{project_id}:frontend_api_call:{rel}:{name}"
            if node_id in seen:
                continue
            seen.add(node_id)
            mo = re.search(rf"\b{re.escape(name)}\s*=", text)
            line = text.count("\n", 0, mo.start()) + 1 if mo else 1
            nodes.append(GraphNode(
                id=node_id, kind=NodeKind.FRONTEND_API_CALL.value, name=name,
                project_id=project_id, file=rel, line=line, language="javascript",
                meta={"url": path, "http_method": "POST", "url_registry": True},
            ))
    return nodes


def _safe_read(f: Path) -> str:
    try:
        return f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
