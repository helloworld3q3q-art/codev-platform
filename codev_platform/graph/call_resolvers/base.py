"""调用边(CALLS)解析器框架 —— 按语言栈可扩展, cross-node post-pass。

plugins 各栈 scan 产出节点(endpoint / function / ...)后, resolver 解析它们之间的调用关系
(endpoint→function / 函数→函数, 含 service→store), 产中性 EdgeKind.CALLS 边。

为何独立于 AnalyzerPlugin(而非让 plugin 兼产 calls):calls 是 **cross-node** 解析, 需要
**全量已落库 nodes** 做 join, 而 plugin.analyze 在落库前单独跑、彼此看不到对方产出。故
resolver 是 ingest 的 post-pass(同 _link_pass 的位置), 不是 per-plugin per-repo 扫描。

加语言 = 加一个 CallResolver 实现 + register_resolver 一行, 核心零改(同 agent provider /
plugins 的协议族 + registry 铁律, 零 if-else)。各 resolver 对齐现有插件的语言栈
(spring / fastapi / node / dotnet / ...)。
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from codev_platform.graph.schema import GraphEdge, GraphNode


@runtime_checkable
class CallResolver(Protocol):
    """调用边解析器契约。各语言栈一个实现, 核心(ingest)只依赖本协议。

    name:     resolver 唯一标识(如 "codegraph" / "spring" / "fastapi")。
    applies:  廉价判断本 resolver 是否适用(技术栈命中 + 有可连节点)。
    resolve:  解析调用边, 产 EdgeKind.CALLS 边;失败 fail-soft 返回 [](不抛, 不拖垮 ingest)。
    """

    name: str

    def applies(self, repo: Path, nodes: list[GraphNode]) -> bool: ...

    def resolve(self, repo: Path, project_id: str, nodes: list[GraphNode]) -> list[GraphEdge]: ...


_RESOLVERS: list[CallResolver] = []


def register_resolver(r: CallResolver) -> None:
    """注册一个 resolver —— 加语言栈的唯一接入点(在 call_resolvers/__init__ 调)。

    去重优先级 = confidence(见 _calls_pass): 同一 (source,target,kind) 边保留**置信最高者**。
    注册顺序仅作 **confidence 并列时的 tiebreak**(先注册者赢)。故跨语言兜底者(codegraph,
    边 conf<1.0)先注册, 各语言专门 resolver(精确解析 conf=1.0)后注册, 能正确盖过兜底的同边;
    而 codegraph 追到、专门 resolver 追不到的边(无人竞争)原样保留。
    """
    _RESOLVERS.append(r)


def registered_resolvers() -> list[CallResolver]:
    """全部已注册 resolver(测试 / 审计用)。"""
    return list(_RESOLVERS)


def applicable_resolvers(repo: Path, nodes: list[GraphNode]) -> list[CallResolver]:
    """筛出适用本仓 + 节点集的 resolver(applies 命中)。applies 抛错的视为不适用(fail-soft)。"""
    out: list[CallResolver] = []
    for r in _RESOLVERS:
        try:
            if r.applies(repo, nodes):
                out.append(r)
        except Exception:  # noqa: BLE001 — applies 是廉价探测, 出错即视为不适用, 不拖垮 pass
            continue
    return out
