"""业务域标注器的生产实现(A1-2b)—— 唯一调 brain LLM 的地方。

实现 DomainLabeler 协议: 把 ClusterRequest 渲染成 prompt → brain get_provider().chat →
韧性解析 LLM 文本回 ClusterLabel。LLM 不确定性全锁在本文件, A1-2a 的聚类/解析/缓存不受影响。

grounding(双层, 与 A1-1 validate 正交):
- prompt 层: closed-world 清单 + "members 必须是清单 ref, 禁引入清单外实体";
- 解析层: member_refs 必 ⊆ 该 cluster 的 ref(越界剔除), cluster 用短号 c1/c2 回引(防串台)。
LLM 输出韧性递降解析(直接 JSON → 围栏 → 抠数组), 全失败 fail-soft 整批 domain=None。

无 key / 未装 agent extra → available()=False → BusinessDomainAnalyzer.applies 返回 False
(no-op), 生产不会拿一个废 labeler 乱标。
"""
from __future__ import annotations

import json
import logging
import re

from codev_platform.graph.analyzers.domain_labeler import ClusterLabel, ClusterRequest

logger = logging.getLogger(__name__)

PROMPT_VERSION = "1"

_SYSTEM = (
    "你是代码库业务域标注者, 只归类不发现。规则:\n"
    "1. 只能用下方清单里的实体, members 必须是清单中的 ref(如 e1/t2), "
    "禁止引入清单外的表/接口/概念;\n"
    "2. 域名用中文业务术语 2-6 字(如 订单/行情/资金流/用户鉴权), "
    "禁造词、禁英文、禁拼音、禁'模块/系统/管理'等冗余后缀;\n"
    "3. 每个 cluster 给一个主域; 无法归纳则 domain 留 null;\n"
    "4. 严格输出 JSON 数组, 无多余文字。"
)

_PROMPT_TEMPLATE = (
    "给下列每个 cluster 归纳一个业务域。\n\n"
    "示例输入:\n"
    "cluster c1:\n  e1 (endpoint): GET /api/orders/{{id}}\n  t1 (table): orders\n"
    "示例输出:\n"
    '[{{"cluster":"c1","domain":"订单","members":["e1"]}}]\n\n'
    "实际输入:\n{clusters}\n\n"
    "输出 JSON 数组:"
)


class BrainDomainLabeler:
    """经 brain 多 provider 路由的业务域标注器(纯标注, 不走 loop / 工具)。"""

    def __init__(self, cfg: dict | None = None, *, provider=None) -> None:
        # provider 注入用于测试(mock); 生产留 None 走 get_provider 懒构建。
        self._cfg = cfg
        self._provider = provider
        self._resolved = provider is not None

    @property
    def signature(self) -> str:
        """缓存键的模型维度: model + prompt_version。换模型/改 prompt 必变 → 缓存失效。"""
        prov = self._get_provider()
        model = getattr(prov, "model", "none") if prov is not None else "none"
        return f"business_domain:{model}:v{PROMPT_VERSION}"

    def available(self) -> bool:
        """provider 可构建(key 在位 + agent extra 已装)。否则 analyzer no-op。"""
        return self._get_provider() is not None

    def label(self, batch: list[ClusterRequest]) -> list[ClusterLabel]:
        prov = self._get_provider()
        if prov is None:
            return [ClusterLabel(req.cluster_id, None) for req in batch]

        cid_map = {f"c{i + 1}": req.cluster_id for i, req in enumerate(batch)}
        req_by_short = {f"c{i + 1}": req for i, req in enumerate(batch)}
        prompt = self._render(batch)
        try:
            from codev_platform.agent.brain.types import Message
            turn = prov.chat(_SYSTEM, [Message(role="user", content=prompt)], [])
            text = turn.text
        except Exception as exc:  # noqa: BLE001 — provider 抖动 fail-soft, 不拖垮 ingest
            logger.warning("[business_domain] chat failed: %r", exc)
            return [ClusterLabel(req.cluster_id, None) for req in batch]

        parsed = self._parse(text)
        out: list[ClusterLabel] = []
        seen: set[str] = set()
        for item in parsed:
            short = item.get("cluster")
            real = cid_map.get(short)
            if real is None or real in seen:
                continue  # 未知/重复 cluster 短号 → 丢(防串台)
            seen.add(real)
            valid = {m.ref for m in req_by_short[short].members}
            members = tuple(r for r in (item.get("members") or []) if r in valid)
            domain = item.get("domain")
            out.append(ClusterLabel(real, domain if isinstance(domain, str) else None,
                                    members))
        for short, real in cid_map.items():  # 模型漏掉的 cluster → None(fail-soft)
            if real not in seen:
                out.append(ClusterLabel(real, None))
        return out

    # ---- 内部 ----

    def _get_provider(self):
        if self._resolved:
            return self._provider
        self._resolved = True
        try:
            from codev_platform.agent.brain.registry import get_provider
            self._provider = get_provider(self._cfg)
        except Exception as exc:  # noqa: BLE001 — 缺 key/extra → 不可用(None), 不报错
            logger.debug("[business_domain] provider unavailable: %r", exc)
            self._provider = None
        return self._provider

    @staticmethod
    def _render(batch: list[ClusterRequest]) -> str:
        lines: list[str] = []
        for i, req in enumerate(batch):
            lines.append(f"cluster c{i + 1}:")
            for m in req.members:
                lines.append(f"  {m.ref} ({m.kind}): {m.name}")
        return _PROMPT_TEMPLATE.format(clusters="\n".join(lines))

    @staticmethod
    def _parse(text: str | None) -> list[dict]:
        """韧性递降: 直接 JSON → ```围栏 → 抠第一个 [...]。全失败回 []。"""
        if not text:
            return []
        candidates = [text]
        fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S)
        if fence:
            candidates.append(fence.group(1))
        arr = re.search(r"\[.*\]", text, re.S)
        if arr:
            candidates.append(arr.group(0))
        for c in candidates:
            try:
                data = json.loads(c)
            except ValueError:
                continue
            if isinstance(data, list):
                return [d for d in data if isinstance(d, dict)]
        return []
