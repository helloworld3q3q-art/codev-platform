"""前端→后端 API 链接标注器的生产实现(A3-2)—— 唯一调 brain LLM 的地方(对称 brain_layer_labeler)。

实现 ApiLinkLabeler 协议: 把 ApiLinkRequest(前端文件源码片段 + 候选后端端点清单)渲染成 prompt
→ brain get_provider().chat → 韧性解析 LLM 文本回 ApiLinkLabel。LLM 不确定性全锁在本文件,
analyzer 侧的盲文件筛选 / 源码裁剪 / 候选编号 / 回填不受影响。

grounding(双层, 与 base.validate_soft_result 正交):
- prompt 层: closed-world(只能引清单内 ep ref, 禁造端点);宁缺毋滥(看不出调哪个就不选);
- 解析层: ep ref 必 ∈ 该文件候选(越界剔除), 文件短号 f1/f2 回引(防串台)。
LLM 输出韧性递降解析(直接 JSON → 围栏 → 抠数组), 全失败 fail-soft 该文件 endpoint_refs=()。

无 key / 未装 agent extra → available()=False → FrontendApiLinkAnalyzer.applies 返回 False(no-op)。
"""
from __future__ import annotations

import json
import logging
import re

from codev_platform.graph.analyzers.api_link_labeler import (
    ApiLinkLabel,
    ApiLinkRequest,
)

logger = logging.getLogger(__name__)

PROMPT_VERSION = "1"

_SYSTEM = (
    "你是前端→后端 API 调用链接标注者, 只做匹配不做发现。给你一个前端文件的请求相关源码片段 + "
    "一份候选后端端点清单(每个有短号 ep1/ep2 + url + method)。判断该前端文件**实际调用了**清单里"
    "的哪些端点。规则:\n"
    "1. 只能选清单里出现的 ep 短号, 禁造端点 / 禁选清单外的;\n"
    "2. 依据是源码里的 url 路径 / 业务命名 / method 与候选端点的对应关系;\n"
    "3. 宁缺毋滥: 看不出明确对应就不选(一个文件可以一个端点都不调);\n"
    "4. 一个文件可调多个端点;\n"
    "5. 严格输出 JSON 数组, 无多余文字。"
)

_PROMPT_TEMPLATE = (
    "候选后端端点清单(全部文件共用):\n{endpoints}\n\n"
    "下列每个前端文件(短号 f1/f2), 判断它调了哪些 ep 短号。\n\n"
    "示例输出格式:\n"
    '[{{"file":"f1","endpoints":["ep3","ep7"]}},{{"file":"f2","endpoints":[]}}]\n\n'
    "实际前端文件:\n{files}\n\n输出 JSON 数组:"
)


class BrainApiLinkLabeler:
    """经 brain 多 provider 路由的 API 链接标注器(纯标注, 不走 loop / 工具)。"""

    def __init__(self, cfg: dict | None = None, *, provider=None) -> None:
        self._cfg = cfg
        self._provider = provider
        self._resolved = provider is not None

    @property
    def signature(self) -> str:
        prov = self._get_provider()
        model = getattr(prov, "model", "none") if prov is not None else "none"
        return f"frontend_api_link:{model}:v{PROMPT_VERSION}"

    def available(self) -> bool:
        return self._get_provider() is not None

    def label(self, batch: list[ApiLinkRequest]) -> list[ApiLinkLabel]:
        prov = self._get_provider()
        if prov is None:
            return [ApiLinkLabel(req.file.path) for req in batch]

        fid_map = {f"f{i + 1}": req.file.path for i, req in enumerate(batch)}
        req_by_short = {f"f{i + 1}": req for i, req in enumerate(batch)}
        prompt = self._render(batch)
        try:
            from codev_platform.agent.brain.types import Message
            turn = prov.chat(_SYSTEM, [Message(role="user", content=prompt)], [])
            text = turn.text
        except Exception as exc:  # noqa: BLE001 — provider 抖动 fail-soft, 不拖垮 ingest
            logger.warning("[frontend_api_link] chat failed: %r", exc)
            return [ApiLinkLabel(req.file.path) for req in batch]

        parsed = self._parse(text)
        out: list[ApiLinkLabel] = []
        seen: set[str] = set()
        for item in parsed:
            short = item.get("file")
            real = fid_map.get(short)
            if real is None or real in seen:
                continue  # 未知/重复文件短号 → 丢(防串台)
            seen.add(real)
            valid_refs = {e.ref for e in req_by_short[short].endpoints}
            refs: list[str] = []
            for r in item.get("endpoints") or []:
                if isinstance(r, str) and r in valid_refs and r not in refs:
                    refs.append(r)            # 越界 ep ref(造的假端点)→ 剔除
            out.append(ApiLinkLabel(real, tuple(refs)))
        for _short, real in fid_map.items():  # 模型漏掉的文件 → endpoint_refs=()(fail-soft)
            if real not in seen:
                out.append(ApiLinkLabel(real))
        return out

    # ---- 内部 ----

    def _get_provider(self):
        if self._resolved:
            return self._provider
        self._resolved = True
        try:
            from codev_platform.agent.brain.registry import get_provider
            from codev_platform.core.config import get, load_config

            cfg = self._cfg if self._cfg is not None else load_config()
            # A3 可独立选模型(标注用便宜/本地的): analyzers.frontend_api_link.provider 覆盖全局。
            ap = get(cfg, "analyzers.frontend_api_link.provider")
            if ap:
                cfg = {**cfg, "agent": {**cfg.get("agent", {}), "provider": ap}}
            self._provider = get_provider(cfg)
        except Exception as exc:  # noqa: BLE001 — 缺 key/extra → 不可用(None), 不报错
            logger.debug("[frontend_api_link] provider unavailable: %r", exc)
            self._provider = None
        return self._provider

    @staticmethod
    def _render(batch: list[ApiLinkRequest]) -> str:
        # 端点清单全批共用(取首个 req 的候选; analyzer 给每个 req 喂同一份全局候选)。
        ep_lines: list[str] = []
        if batch:
            for e in batch[0].endpoints:
                ep_lines.append(f"  {e.ref}: {e.method} {e.url}  ({e.name})")
        file_lines: list[str] = []
        for i, req in enumerate(batch):
            file_lines.append(f"f{i + 1}: {req.file.path}")
            for ln in req.file.snippet.splitlines():
                file_lines.append(f"    {ln}")
        return _PROMPT_TEMPLATE.format(
            endpoints="\n".join(ep_lines), files="\n".join(file_lines))

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
