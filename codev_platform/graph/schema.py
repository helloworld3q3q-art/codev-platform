"""统一图谱模型 — 插件与核心之间的通用货币 (Phase 1).

定位:把 codev-platform 从固定工具集合升级为"模块化核心 + 插件化扩展"全链路
AI 平台时,所有 analyzer 插件 (Frontend / Backend / Database / Connector / CodeGraph /
CrossLink adapter) 都必须输出统一模型,平台核心据此存储、查询、给 Agent 使用:

    GraphNode  发现了什么对象
    GraphEdge  对象之间有什么关系
    Evidence   结论来自哪里
    Finding    插件发现的风险、影响或异常
    AnalyzerResult  一次插件分析的完整产出 (nodes + edges + evidences + findings)

风格:与 agent/brain/types.py 一致,用 dataclass 作"中性类型"(模块间传递的领域货币),
而非 pydantic BaseModel (后者在本仓专用于 HTTP 请求/响应)。本文件只定义 schema,
不依赖 cross-link / codegraph 的现有存储,适配器 (把现有输出转成本模型) 留到后续。

序列化:每个类型提供 to_dict() / from_dict(),用于跨进程传递 + sqlite/JSON 落盘。
NodeKind / EdgeKind 是开放枚举 (str 子类),核心值由 plan 列出,插件可在不破坏存储的
前提下扩展,但建议优先复用已有 kind 以保证跨插件可链接。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class NodeKind(str, Enum):
    """统一节点类型 (plan §Phase 1 节点清单).

    str 子类 -> 既可当枚举又可直接当字符串用 (JSON / sqlite 友好)。
    插件遇到本枚举未覆盖的对象时可用裸字符串 kind,但优先复用这里的值。
    """

    PROJECT = "project"
    FILE = "file"
    FRONTEND_ROUTE = "frontend_route"
    FRONTEND_COMPONENT = "frontend_component"
    # 前端模块依赖图节点(dependency-cruiser 文件级模块): 区别于 vue 的 FRONTEND_COMPONENT(SFC 语义),
    # 这是"A 文件 import B 文件"的依赖图, 解锁"改组件→影响哪些页面"(codegraph 盲区)。
    FRONTEND_MODULE = "frontend_module"
    FRONTEND_API_CALL = "frontend_api_call"
    BACKEND_ENDPOINT = "backend_endpoint"
    BACKEND_FUNCTION = "backend_function"
    DB_TABLE = "db_table"
    DB_COLUMN = "db_column"
    WIKI_PAGE = "wiki_page"
    JIRA_ISSUE = "jira_issue"
    FEISHU_DOC = "feishu_doc"
    GIT_COMMIT = "git_commit"
    PULL_REQUEST = "pull_request"


class EdgeKind(str, Enum):
    """统一边类型 (plan §Phase 1 边清单)。"""

    CONTAINS = "contains"
    IMPORTS = "imports"
    CALLS = "calls"
    RENDERS = "renders"
    DEFINES_API = "defines_api"
    CALLS_API = "calls_api"
    IMPLEMENTS = "implements"
    READS_TABLE = "reads_table"
    WRITES_TABLE = "writes_table"
    UPDATES_TABLE = "updates_table"
    MENTIONS = "mentions"
    RELATES_TO = "relates_to"
    CHANGED_BY = "changed_by"


def _kind_str(value: Any) -> str:
    """把 NodeKind/EdgeKind 枚举或裸字符串统一成 str (序列化用)。"""
    return value.value if isinstance(value, Enum) else str(value)


@dataclass
class GraphNode:
    """统一节点:平台图谱里的一个对象。

    id:       全局唯一标识 (建议 "<project_id>:<kind>:<stable-key>" 保证跨插件可链接)。
    kind:     NodeKind 枚举值或裸字符串 (开放枚举)。
    name:     人类可读名称 (函数名 / 表名 / 路由路径 / 文档标题 ...)。
    project_id: 所属项目 (多租户隔离)。
    file:     源文件路径 (相对仓库根),代码类节点必填,文档/外部资源类可空。
    line:     源文件行号 (可空)。
    language: 语言 / 技术栈 (java / python / typescript / vue / sql ...);非代码节点可空。
    meta:     插件专属字段不透明袋子 (不污染中性 schema;落盘成 JSON)。
    """

    id: str
    kind: str
    name: str
    project_id: str
    file: str | None = None
    line: int | None = None
    language: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = _kind_str(self.kind)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GraphNode:
        return cls(
            id=d["id"],
            kind=_kind_str(d["kind"]),
            name=d["name"],
            project_id=d["project_id"],
            file=d.get("file"),
            line=d.get("line"),
            language=d.get("language"),
            meta=dict(d.get("meta") or {}),
        )


@dataclass
class GraphEdge:
    """统一边:两个节点之间的一条有向关系。

    source / target: 端点 GraphNode.id。
    kind:       EdgeKind 枚举值或裸字符串 (开放枚举)。
    confidence: 置信度,精确锚点=1.0,降级 fuzzy 识别<1.0 (与现有 cross-link 一致)。
    meta:       插件专属字段 (如 evidence_ids 引用、命中规则名 ...)。
    """

    source: str
    target: str
    kind: str
    confidence: float = 1.0
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = _kind_str(self.kind)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GraphEdge:
        return cls(
            source=d["source"],
            target=d["target"],
            kind=_kind_str(d["kind"]),
            confidence=float(d.get("confidence", 1.0)),
            meta=dict(d.get("meta") or {}),
        )


@dataclass
class Evidence:
    """结论来源:某条 node/edge/finding 为什么成立。

    source:  证据来源标识 (插件名 / 扫描器 / connector,如 "builtin.cross_link")。
    detail:  人类可读说明 (命中的代码片段、规则、匹配文本)。
    file:    证据所在文件 (可空)。
    line:    证据所在行号 (可空)。
    confidence: 该证据本身的可信度。
    meta:    额外字段 (如 snippet、规则 id)。
    """

    source: str
    detail: str
    file: str | None = None
    line: int | None = None
    confidence: float = 1.0
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Evidence:
        return cls(
            source=d["source"],
            detail=d["detail"],
            file=d.get("file"),
            line=d.get("line"),
            confidence=float(d.get("confidence", 1.0)),
            meta=dict(d.get("meta") or {}),
        )


@dataclass
class Finding:
    """插件发现的风险 / 影响 / 异常。

    kind:      finding 分类 (如 "impact" / "risk" / "anomaly" / "missing_link")。
    severity:  严重程度 ("info" | "low" | "medium" | "high" | "critical")。
    title:     一句话标题。
    detail:    详细说明。
    node_ids:  关联节点 id 列表 (此 finding 涉及哪些对象)。
    evidence_ids: 关联证据 (此 finding 由哪些 Evidence 支撑;按 list 下标或外部 id)。
    meta:      额外字段。
    """

    kind: str
    severity: str
    title: str
    detail: str = ""
    node_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Finding:
        return cls(
            kind=d["kind"],
            severity=d["severity"],
            title=d["title"],
            detail=d.get("detail", ""),
            node_ids=list(d.get("node_ids") or []),
            evidence_ids=list(d.get("evidence_ids") or []),
            meta=dict(d.get("meta") or {}),
        )


@dataclass
class AnalyzerResult:
    """一次插件分析的完整产出。

    所有 analyzer 插件的 analyze() 必须返回本类型;平台核心据此入统一图谱。
    plugin / plugin_version 标注产出来源,便于审计 + 增量 reindex 归因。
    """

    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    evidences: list[Evidence] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    plugin: str | None = None
    plugin_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "evidences": [ev.to_dict() for ev in self.evidences],
            "findings": [f.to_dict() for f in self.findings],
            "plugin": self.plugin,
            "plugin_version": self.plugin_version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AnalyzerResult:
        return cls(
            nodes=[GraphNode.from_dict(x) for x in d.get("nodes") or []],
            edges=[GraphEdge.from_dict(x) for x in d.get("edges") or []],
            evidences=[Evidence.from_dict(x) for x in d.get("evidences") or []],
            findings=[Finding.from_dict(x) for x in d.get("findings") or []],
            plugin=d.get("plugin"),
            plugin_version=d.get("plugin_version"),
        )

    def merge(self, other: AnalyzerResult) -> None:
        """把另一份结果并入本结果 (核心聚合多插件产出时用)。"""
        self.nodes.extend(other.nodes)
        self.edges.extend(other.edges)
        self.evidences.extend(other.evidences)
        self.findings.extend(other.findings)
