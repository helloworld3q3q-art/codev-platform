"""统一图谱模型 — 插件与核心之间的通用货币 (Phase 1).

定位:把 codev-platform 从固定工具集合升级为"模块化核心 + 插件化扩展"全链路
AI 平台时,所有 analyzer 插件 (Frontend / Backend / Database / Connector / CodeGraph)
都必须输出统一模型,平台核心据此存储、查询、给 Agent 使用:

    GraphNode  发现了什么对象
    GraphEdge  对象之间有什么关系
    Evidence   结论来自哪里
    Finding    插件发现的风险、影响或异常
    AnalyzerResult  一次插件分析的完整产出 (nodes + edges + evidences + findings)

风格:与 agent/brain/types.py 一致,用 dataclass 作"中性类型"(模块间传递的领域货币),
而非 pydantic BaseModel (后者在本仓专用于 HTTP 请求/响应)。本文件只定义 schema,
不依赖具体插件 (builtin.sql / stack 插件等) 的实现细节。

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
    # 软节点(分析器/LLM 派生, 非确定性血缘): 业务域归类。confidence<1.0, impact 默认过滤,
    # 与硬节点(plugins 确定性产)物理可分辨 —— 保护"查依赖"不被 LLM 噪声污染。
    BUSINESS_DOMAIN = "business_domain"
    # 软节点(A2 综合理解层): 架构分层角色(controller/service/repository/...)。与 BUSINESS_DOMAIN
    # 同样软隔离(confidence<1.0 + impact 默认过滤), 但维度正交: 一个 file 既属某域又演某层。
    ARCH_LAYER = "arch_layer"
    # 软节点(A3 综合理解层): LLM 推断的前端 API 调用。当静态层(url_registry / 内联扫描)抓不到
    # 业务封装的 request({url}) 调用时, A3 读前端源码语义推断"该前端文件调哪个后端端点"。与硬的
    # FRONTEND_API_CALL(确定性字面量/常量解析)物理可分辨: confidence<1.0 + impact 默认过滤, 防
    # LLM 推断污染"查依赖"。grounding: 必须 resolve 回真实后端 endpoint, 否则丢。
    INFERRED_API_CALL = "inferred_api_call"
    # 软节点(Phase 4 结构社区): 算法(Louvain)派生的结构聚类, 给**全节点**派社区号(免 LLM、
    # 确定性、全 kind 覆盖)。与 BUSINESS_DOMAIN(LLM 语义域, 只 endpoint/表)正交互补: 一个节点
    # 既属某结构社区又可属某业务域。软隔离(impact 默认过滤): 社区是分组叠加层非依赖关系。
    COMMUNITY = "community"


class EdgeKind(str, Enum):
    """统一边类型 (plan §Phase 1 边清单)。"""

    CONTAINS = "contains"
    IMPORTS = "imports"
    CALLS = "calls"
    RENDERS = "renders"
    DEFINES_API = "defines_api"
    CALLS_API = "calls_api"
    # 页面/组件 --uses_api--> url_registry 型 frontend_api_call 常量(页面源码静态引用了该常量名)。
    # 集中声明 API 常量(URL.js)的项目里, "页面 import 整个注册模块"≠"调用其每个接口" → 用本边
    # 精确归因到真正引用了某常量的页面, 取代"经共享模块 contains 泛连"的过报(见 impact 注释)。
    USES_API = "uses_api"
    IMPLEMENTS = "implements"
    READS_TABLE = "reads_table"
    WRITES_TABLE = "writes_table"
    UPDATES_TABLE = "updates_table"
    MENTIONS = "mentions"
    RELATES_TO = "relates_to"
    CHANGED_BY = "changed_by"
    # 软边(分析器派生): 硬节点 --belongs_to_domain--> BUSINESS_DOMAIN 软节点。
    BELONGS_TO_DOMAIN = "belongs_to_domain"
    # 软边(A2): file 硬节点 --plays_role--> ARCH_LAYER 软节点(该文件演哪个架构层角色)。
    PLAYS_ROLE = "plays_role"
    # 软边(A3): INFERRED_API_CALL 软节点 --calls_api_inferred--> backend_endpoint 硬节点。
    # 语义同 CALLS_API(前端调后端)但来源是 LLM 推断而非静态解析 → 软隔离(confidence<1.0 +
    # impact 默认过滤), 与确定性 CALLS_API 区分: 查依赖默认只信静态边, 推断边作候选提示。
    CALLS_API_INFERRED = "calls_api_inferred"
    # 软边(Phase 4): 硬节点 --in_community--> COMMUNITY 软节点(该节点属哪个结构社区)。
    # 社区是结构聚类的"分组叠加层", 非依赖关系 → 软隔离(impact 默认过滤), 不污染查依赖。
    IN_COMMUNITY = "in_community"


def _kind_str(value: Any) -> str:
    """把 NodeKind/EdgeKind 枚举或裸字符串统一成 str (序列化用)。"""
    return value.value if isinstance(value, Enum) else str(value)


# 软节点/软边 (分析器/LLM 派生, 非确定性血缘) 的 kind 集合 + 判定。
# 用途: ① impact 默认过滤软边 (查依赖护城河不被 LLM 噪声污染);
#       ② ingest referential-integrity 校验 (软边端点必须指向真实硬节点, 悬空即丢)。
# 软节点判据不止 kind (还有 confidence<1.0 + meta.derived_by), 但 kind 是最直接的物理标记。
SOFT_NODE_KINDS: frozenset[str] = frozenset(
    {NodeKind.BUSINESS_DOMAIN.value, NodeKind.ARCH_LAYER.value,
     NodeKind.INFERRED_API_CALL.value, NodeKind.COMMUNITY.value})
SOFT_EDGE_KINDS: frozenset[str] = frozenset(
    {EdgeKind.BELONGS_TO_DOMAIN.value, EdgeKind.PLAYS_ROLE.value,
     EdgeKind.CALLS_API_INFERRED.value, EdgeKind.IN_COMMUNITY.value})


def is_soft_node_kind(kind: Any) -> bool:
    return _kind_str(kind) in SOFT_NODE_KINDS


def is_soft_edge_kind(kind: Any) -> bool:
    return _kind_str(kind) in SOFT_EDGE_KINDS


def dedup_nodes_by_id(nodes: list) -> list:
    """同 id 多节点去重 —— 不同插件描述同一文件会撞同一 id(如 builtin.vue 的 frontend_component
    与 builtin.frontend_deps 的 frontend_module 对同一 .vue: id 全同 kind 不同, 实测 129 文件双份)。
    合一保留语义更强的(frontend_module 是 import 依赖图的弱视图, 让位给 frontend_component)。
    边按 id 引用, 去重后 imports/renders 边仍正确解析。"""
    by_id: dict = {}
    for n in nodes:
        ex = by_id.get(n.id)
        if ex is None:
            by_id[n.id] = n
        elif (_kind_str(ex.kind) == NodeKind.FRONTEND_MODULE.value
              and _kind_str(n.kind) != NodeKind.FRONTEND_MODULE.value):
            by_id[n.id] = n   # 弱 kind(frontend_module)让位给更强的
    return list(by_id.values())


# ---------------------------------------------------------------- provenance (Phase 3)
# 让影响分析的每条边**可追溯到来源**: 是 AST 精确解析来的, 还是框架语义/结构桥接/名称
# 启发式/LLM 推断来的。高风险影响结论应只采信确定来源 + 高置信边, 低置信/启发式边作候选提示。
# 落点: edge.meta[PROV_KEY](走 meta_json 往返, **零 sqlite 迁移**)。confidence 仍是
# GraphEdge.confidence 一等字段; provenance 解释"这条边的置信为何如此"。


class ProvSource(str, Enum):
    """边来源类 —— 描述边的**派生方法**(plan §Phase 3 source_kind 精简集)。

    与置信**正交**: "确定依赖 vs 候选"由 confidence 定(见 impact._CERTAIN_CONF), 不由 src 定。
    同一 src 可有不同置信(如 regex: SQL DDL 解析 conf=1.0 确定 / DI 名称 BFS conf<1.0 候选)。

    ast       结构化 AST / 工具解析 (codegraph 调用图 / dependency-cruiser 依赖图)
    framework 框架语义适配 (linker: frontend_api_call→endpoint 按路由匹配)
    bridge    结构桥接 (frontend module→同文件 api_call/route)
    regex     正则 / 模式解析 (SQL DDL / 前端 import-call / DI 名称 BFS; 精度由 confidence 承载)
    llm       分析器 / LLM 软边 (belongs_to_domain / plays_role)
    manual    人工标注
    """

    AST = "ast"
    FRAMEWORK = "framework"
    BRIDGE = "bridge"
    REGEX = "regex"
    LLM = "llm"
    MANUAL = "manual"


# edge.meta 里 provenance 子袋的标准 key(嵌套一层避免与插件已用 meta key 撞:
# fastapi 的 resolver/via_handler、前端的 is_page 等)。
PROV_KEY: str = "prov"


def stamp_provenance(edge: GraphEdge, src: Any, *,
                     parser: str | None = None, pv: str | None = None) -> GraphEdge:
    """给一条边盖 provenance 戳(原地写 edge.meta[PROV_KEY], 返回同一 edge 便于链式)。

    src:    ProvSource 枚举或裸字符串(来源类)。
    parser: 产这条边的解析器/插件名(如 "codegraph" / "fastapi" / "builtin.linker")。
    pv:     解析器版本(可空; 暂多数无版本概念)。
    既有 meta 字段保留; 重复盖戳幂等覆盖 PROV_KEY 子袋。
    """
    prov: dict[str, Any] = {"src": _kind_str(src)}
    if parser:
        prov["parser"] = parser
    if pv:
        prov["pv"] = pv
    edge.meta = dict(edge.meta or {})
    edge.meta[PROV_KEY] = prov
    return edge


def edge_provenance(meta: dict[str, Any] | None) -> dict[str, Any]:
    """从 edge.meta 取 provenance 子袋(无则 {})。读侧(impact/audit)统一入口。"""
    return dict((meta or {}).get(PROV_KEY) or {})


def stamp_unprovenanced(edges: list[GraphEdge], src: Any, *,
                        parser: str | None = None, pv: str | None = None) -> None:
    """给一批边里**尚未盖 provenance** 的逐条盖默认来源戳(原地)。

    已盖戳的保留 —— 支持 producer 局部覆盖默认值(分层: producer 精确盖优先, 边界统一兜底)。
    用于在执行/聚合边界按 producer 声明的来源批量归因(executor / ingest pass)。
    """
    for e in edges:
        if not edge_provenance(e.meta):
            stamp_provenance(e, src, parser=parser, pv=pv)


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
    confidence: 置信度,精确锚点=1.0,降级 fuzzy 识别<1.0 (栈插件粗粒度推断用)。
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

    source:  证据来源标识 (插件名 / 扫描器 / connector,如 "builtin.sql")。
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
