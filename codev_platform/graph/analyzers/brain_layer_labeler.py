"""架构分层标注器的生产实现(A2-2)—— 唯一调 brain LLM 的地方(对称 brain_labeler)。

实现 LayerLabeler 协议: 把 LayerRequest 渲染成 prompt → brain get_provider().chat → 韧性解析
LLM 文本回 LayerLabel。LLM 不确定性全锁在本文件, A2-1 的事实构建/聚类/回填不受影响。

grounding(双层, 与 base.validate_soft_result 正交):
- prompt 层: closed-world(只能用清单 file ref + 固定 LAYER_ROLES 词表里的 layer, 禁清单外);
- 解析层: file ref 必 ∈ 该 batch、layer 必 ∈ LAYER_ROLES(双越界剔除), batch 短号 b1/b2 回引(防串台)。
LLM 输出韧性递降解析(直接 JSON → 围栏 → 抠数组), 全失败 fail-soft 整批 roles=()。

无 key / 未装 agent extra → available()=False → ArchLayerAnalyzer.applies 返回 False(no-op)。
"""
from __future__ import annotations

import json
import logging
import re

from codev_platform.graph.analyzers.layer_labeler import (
    FRONTEND_LAYER_ROLES,
    LAYER_ROLES,
    LayerLabel,
    LayerRequest,
)

logger = logging.getLogger(__name__)

PROMPT_VERSION = "4"   # v4(2026-06-08): 后端恢复 v2 精度(domain_model=纯数据结构无逻辑, 防 status 误判)+ 保留 v3 前端词表

_BE_ROLES = " / ".join(sorted(LAYER_ROLES))
_FE_ROLES = " / ".join(sorted(FRONTEND_LAYER_ROLES))

_SYSTEM = (
    "你是代码架构分层标注者, 只归类不发现。每个 file 标了 [backend] 或 [frontend], 用对应词表:\n"
    f"  后端词表(backend file 只选这些): {_BE_ROLES};\n"
    f"  前端词表(frontend file 只选这些): {_FE_ROLES};\n"
    "规则:\n"
    "1. backend file 只能用后端词表、frontend file 只能用前端词表, 不混用;\n"
    "2. 后端判据(注意区分 service / repository / domain_model):\n"
    "   - controller/gateway: 有 endpoint, 处理 HTTP 入站(routes/);\n"
    "   - repository: **直接**数据访问(*_repo/*_store/*_pg、SQL/ORM/表 CRUD);\n"
    "   - service: **业务逻辑编排**(健康检查/状态汇总/校验/协调多依赖), 即使间接碰数据也是 service;\n"
    "   - adapter: 封装外部系统/第三方客户端(client/integration/bridge);\n"
    "   - domain_model: **纯数据结构定义**(dataclass/schema/表定义/实体), 无业务逻辑(有逻辑→service);\n"
    "   - util/config: 纯工具函数 / 配置, 横切无业务;\n"
    "3. 前端判据: page(is_page=yes 页面) / component(可复用组件) / api(services/apis 接口层) / "
    "store(状态管理) / hook(use* 自定义 hook) / util/config;\n"
    "4. file 必须是清单里的 ref, layer 必须在对应词表内, 禁造词;\n"
    "5. 严格输出 JSON 数组, 无多余文字。"
)

_PROMPT_TEMPLATE = (
    "给下列每个目录(batch)里的每个 file 标一个架构层角色(按 file 的 [backend]/[frontend] 选词表)。\n\n"
    "示例输入:\nbatch b1 (dir: web):\n"
    "  f1: routes/orders.py [backend endpoint=yes tables=0 funcs=3]\n"
    "  f2: repositories/order_repo.py [backend endpoint=no tables=2 funcs=6]\n"
    "  f3: services/order_service.py [backend endpoint=no tables=1 funcs=8]\n"
    "  f4: pages/OrderList.tsx [frontend is_page=yes]\n"
    "  f5: components/Button.tsx [frontend is_page=no]\n"
    "  f6: services/apis/orderapi.ts [frontend is_page=no]\n"
    "示例输出(后端 f1-3 用后端词表, 前端 f4-6 用前端词表):\n"
    '[{{"batch":"b1","roles":[{{"file":"f1","layer":"controller"}},{{"file":"f2","layer":"repository"}},'
    '{{"file":"f3","layer":"service"}},{{"file":"f4","layer":"page"}},{{"file":"f5","layer":"component"}},'
    '{{"file":"f6","layer":"api"}}]}}]\n\n'
    "实际输入:\n{batches}\n\n输出 JSON 数组:"
)


class BrainLayerLabeler:
    """经 brain 多 provider 路由的架构层标注器(纯标注, 不走 loop / 工具)。"""

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
        return f"arch_layer:{model}:v{PROMPT_VERSION}"

    def available(self) -> bool:
        """provider 可构建(key 在位 + agent extra 已装)。否则 analyzer no-op。"""
        return self._get_provider() is not None

    def label(self, batch: list[LayerRequest]) -> list[LayerLabel]:
        prov = self._get_provider()
        if prov is None:
            return [LayerLabel(req.batch_id) for req in batch]

        bid_map = {f"b{i + 1}": req.batch_id for i, req in enumerate(batch)}
        req_by_short = {f"b{i + 1}": req for i, req in enumerate(batch)}
        prompt = self._render(batch)
        try:
            from codev_platform.agent.brain.types import Message
            turn = prov.chat(_SYSTEM, [Message(role="user", content=prompt)], [])
            text = turn.text
        except Exception as exc:  # noqa: BLE001 — provider 抖动 fail-soft, 不拖垮 ingest
            logger.warning("[arch_layer] chat failed: %r", exc)
            return [LayerLabel(req.batch_id) for req in batch]

        parsed = self._parse(text)
        out: list[LayerLabel] = []
        seen: set[str] = set()
        for item in parsed:
            short = item.get("batch")
            real = bid_map.get(short)
            if real is None or real in seen:
                continue  # 未知/重复 batch 短号 → 丢(防串台)
            seen.add(real)
            fact_by_ref = {f.ref: f for f in req_by_short[short].files}
            roles: list[tuple[str, str]] = []
            for r in item.get("roles") or []:
                if not isinstance(r, dict):
                    continue
                fref, layer = r.get("file"), r.get("layer")
                ff = fact_by_ref.get(fref)        # 越界 ref(造的假)→ None 剔除
                if ff is None:
                    continue
                allowed = FRONTEND_LAYER_ROLES if ff.is_frontend else LAYER_ROLES
                if layer in allowed:              # 按 file 前后端选词表校验(越界 layer 剔除)
                    roles.append((fref, layer))
            out.append(LayerLabel(real, tuple(roles)))
        for _short, real in bid_map.items():  # 模型漏掉的 batch → roles=()(fail-soft)
            if real not in seen:
                out.append(LayerLabel(real))
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
            # A2 可独立选模型(标注用便宜/本地的): analyzers.arch_layer.provider 覆盖全局 agent.provider。
            ap = get(cfg, "analyzers.arch_layer.provider")
            if ap:
                cfg = {**cfg, "agent": {**cfg.get("agent", {}), "provider": ap}}
            self._provider = get_provider(cfg)
        except Exception as exc:  # noqa: BLE001 — 缺 key/extra → 不可用(None), 不报错
            logger.debug("[arch_layer] provider unavailable: %r", exc)
            self._provider = None
        return self._provider

    @staticmethod
    def _render(batch: list[LayerRequest]) -> str:
        lines: list[str] = []
        for i, req in enumerate(batch):
            lines.append(f"batch b{i + 1} (dir: {req.batch_id}):")
            for f in req.files:
                if f.is_frontend:
                    tag = (f"frontend is_page={'yes' if f.is_page else 'no'} "
                           f"imports_out={f.imports_out} imports_in={f.imports_in}")
                else:
                    ep = "yes" if f.has_endpoint else "no"
                    tag = (f"backend endpoint={ep} tables={f.reads_tables} "
                           f"imports_out={f.imports_out} imports_in={f.imports_in} funcs={f.defines_functions}")
                lines.append(f"  {f.ref}: {f.path} [{tag}]")
        return _PROMPT_TEMPLATE.format(batches="\n".join(lines))

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
