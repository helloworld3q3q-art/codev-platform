"""前端→后端 API 调用标注器接口 —— 把 LLM 不确定性隔离在纯数据契约后(A3 核心隔离点)。

FrontendApiLinkAnalyzer 调本接口给"静态漏检的前端文件"推断它调了哪些后端端点。入参/出参全是
确定性 dataclass(不暴露 brain/Message/provider)—— 候选筛选 / grounding 数据准备 / 解析回填 /
缓存全在 analyzer 侧可测, LLM 措辞差异碰不到这些路径。切分边界(确定性 / LLM)画在这个接口上。

closed-world 进类型: 后端端点编号成 ref(ep1/ep2)喂 labeler, labeler 只能回引 ref —— 模型造一个
不存在的 ep ref, analyzer 解析时直接剔除(防幻觉造端点)。这是"只能从真实端点清单里选"做进数据
结构, 而非仅靠 prompt 自觉。软产物 referential-integrity(悬空软边丢)+ confidence 钳制由
base.validate_soft_result 统一兜底。

实现(两个):
- FakeApiLinkLabeler(测试 / A3-1): 按 URL 子串启发匹配(零网络), 覆盖 analyzer 全确定性路径。
- BrainApiLinkLabeler(A3-2, 生产): 渲染 prompt → brain get_provider().chat → 韧性解析。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class EndpointRef:
    """喂给标注器的一个候选后端端点。ref 是给模型引用的稳定短 ID, 非真 node.id。"""

    ref: str          # "ep1" —— closed-world 引用锚点(造不出新 ref = 造不出新端点)
    url: str          # "/pda/currency/list"
    method: str       # "POST" / "GET"
    name: str         # "POST /pda/currency/list"(人类可读)


@dataclass(frozen=True)
class FrontendFile:
    """一个静态漏检的前端文件 + 其源码片段(请求相关行, 已裁剪防爆 token)。"""

    path: str         # 相对仓库根路径
    snippet: str      # 该文件含 request/api 调用的源码节选(确定性裁剪)


@dataclass(frozen=True)
class ApiLinkRequest:
    """一个前端文件的 API 链接推断请求(closed-world: 文件源码 + 候选端点清单)。"""

    file: FrontendFile
    endpoints: tuple[EndpointRef, ...]   # 候选端点(全局清单的确定性子集/全集)


@dataclass(frozen=True)
class ApiLinkLabel:
    """标注器对一个前端文件的链接归类: 它调了哪些候选端点 ref。"""

    path: str
    endpoint_refs: tuple[str, ...] = ()   # 必 ⊆ 输入 endpoints 的 ref, analyzer 越界剔除


@runtime_checkable
class ApiLinkLabeler(Protocol):
    """前端→后端 API 链接标注契约。入纯数据出纯数据, LLM 完全隔离在实现侧。

    label: 批量标注(一次多文件省 token);失败 fail-soft —— 某文件放弃则该条 endpoint_refs=(),
    不抛(analyzer 不因标注器抖动崩)。返回按 path 重新对齐, 不依赖返回顺序。
    """

    def label(self, batch: list[ApiLinkRequest]) -> list[ApiLinkLabel]: ...


class FakeApiLinkLabeler:
    """确定性测试标注器: 按 URL 子串在 snippet 中出现与否启发匹配(零网络)。

    启发**故意简单**, 只为 A3-1 跑通确定性管线, 不追准确率(真准确率靠 A3-2 BrainApiLinkLabeler
    验收): 候选端点的 url 末段(最后一个 / 后的词)若出现在文件 snippet 里 → 认为调了该端点。
    """

    signature = "fake-apilink-v1"   # 缓存键的一部分(对称 DomainLabeler.signature)

    def label(self, batch: list[ApiLinkRequest]) -> list[ApiLinkLabel]:
        out: list[ApiLinkLabel] = []
        for req in batch:
            low = req.file.snippet.lower()
            refs = tuple(
                ep.ref for ep in req.endpoints
                if ep.url and ep.url.rsplit("/", 1)[-1].lower() in low
            )
            out.append(ApiLinkLabel(req.file.path, refs))
        return out
