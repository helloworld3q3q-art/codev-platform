"""LoopPolicy —— 每模型的循环行为档(策略对象,agent-provider §1/§4 的落点)。

不同模型指令遵从度差异大(强模型少管、弱模型要紧管),把"管多严"做成可按 provider/模型
调的策略,而非写死在 loop.py。loop 只依赖这个中性类型;具体值由 registry 按 provider 解析
(spec 内置默认 ⊕ config 覆盖)。加模型 / 调参 → 只动 config 或 spec 一行,核心循环零改。

字段保持最小、可扩展:后续要加"接近上限强制收尾阈值""是否首轮强制某工具"等再追加字段即可。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LoopPolicy:
    max_steps: int = 12        # 单次问答最多循环步数
    per_tool_cap: int = 4      # 同一工具单轮最多实际执行次数(防弱模型变参 thrash 同一工具)
