"""业务域标注器接口 —— 把 LLM 不确定性隔离在一个纯数据契约后(A1-2 核心隔离点)。

BusinessDomainAnalyzer 调本接口标域名, 接口入参/出参全是确定性 dataclass(不暴露
brain/Message/provider)—— 聚类/grounding/解析/缓存全在 analyzer 侧可测, LLM 措辞差异
碰不到这些路径。切分边界(A1-2a 确定性 / A1-2b LLM)正画在这个接口上。

closed-world 进类型: labeler 只认 ref(短 ID 如 e1/t3)不认真 node.id —— 模型越界引一个
不存在的 ref, analyzer 解析时直接剔除, 把"只能从清单归纳"做进数据结构而非仅靠 prompt。

实现(两个):
- FakeLabeler(测试): 固定查表, 确定性零网络, 覆盖 analyzer 全确定性路径。
- BrainDomainLabeler(A1-2b, 生产): 内部渲染 prompt → brain get_provider().chat → 解析。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ClusterMember:
    """喂给标注器的一个实体(endpoint 或表)。ref 是给模型引用的稳定短 ID, 非真 node.id。"""

    ref: str          # "e1" / "t3" —— closed-world 引用锚点(造不出新 ref = 造不出新实体)
    kind: str         # "endpoint" | "table"
    name: str         # "GET /api/orders/{id}" / "stock_quote_daily"


@dataclass(frozen=True)
class ClusterRequest:
    """一个候选业务域 cluster 的标注请求(纯数据, 喂给 labeler)。"""

    cluster_id: str
    members: tuple[ClusterMember, ...]


@dataclass(frozen=True)
class ClusterLabel:
    """标注器对一个 cluster 的归类结果。"""

    cluster_id: str
    domain: str | None = None          # 中文业务域名; None = labeler 放弃(fail-soft)
    member_refs: tuple[str, ...] = ()  # 模型认为属该域的 ref 子集(必 ⊆ 输入 refs, analyzer 校验越界剔除)


@runtime_checkable
class DomainLabeler(Protocol):
    """业务域标注契约。入纯数据出纯数据, LLM 完全隔离在实现侧。

    label: 批量标注(一次多 cluster 省 token);失败 fail-soft —— 某 cluster 放弃则该条
    domain=None, 不抛(analyzer 不因标注器抖动崩)。返回顺序/cluster_id 由 analyzer 按
    cluster_id 重新对齐, 不依赖返回顺序。
    """

    def label(self, batch: list[ClusterRequest]) -> list[ClusterLabel]: ...
